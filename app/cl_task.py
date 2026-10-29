"""轮询 CL 任务表，把 New 领取为 Process，并复制到本机 task。"""

import logging
import re
import threading
import time

import pymysql
from pymysql.constants import CLIENT

from app.queue_store import close_store, open_store, save_claimed_task

logger = logging.getLogger(__name__)

# 领取时必须能读到的列。国家列由 CL 增补，表上还没有时不读取。
READ_COLUMNS = (
    "AMZ_CROSS_TASK_GUID",
    "TASK_ID",
    "SKU",
    "ASIN",
    "ACTIONS",
    "IMAGES",
    "TASK_STATUS",
)
OPTIONAL_COLUMNS = ("MAIN_COUNTRY", "CROSS_COUNTRY")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# 欧洲五国。每个线程只监听自己的 MAIN_COUNTRY。
COUNTRY_THREADS = (
    ("UK", "英国"),
    ("DE", "德国"),
    ("FR", "法国"),
    ("IT", "意大利"),
    ("ES", "西班牙"),
)
_flush_lock = threading.Lock()
_warned_missing_country = False


def start_polling(config, database_path, picture_log_path=None, aplus_log_path=None):
    """英国、德国、法国、意大利、西班牙各一条线程。每国领自己的 New，做完再领下一条。"""
    from app.heartbeat import heartbeat

    heartbeat.start(config)
    threads = []
    for code, _name in COUNTRY_THREADS:
        thread = threading.Thread(
            target=_country_loop,
            args=(code, config, database_path, picture_log_path, aplus_log_path),
            name="cl-" + code,
            daemon=True,
        )
        thread.start()
        threads.append(thread)
    return threads


def claim_available(config, database_path):
    """领取当前所有 New。更新行数是 1 才写入本机；写入失败则回滚这次领取。"""
    database = _require_db(config)
    table = _identifier(database.get("table"), "cl_db.table")
    connection = _connect(database)
    store = None
    claimed = 0
    try:
        columns = _table_columns(connection, table)
        missing = [name for name in READ_COLUMNS if name not in columns]
        if missing:
            raise RuntimeError("CL 表缺少列：" + "、".join(missing))
        selected = [name for name in READ_COLUMNS if name in columns]
        selected.extend(name for name in OPTIONAL_COLUMNS if name in columns)
        rows = _fetch_new(connection, table, selected)
        store = open_store(database_path)
        for row in rows:
            guid = str(row.get("AMZ_CROSS_TASK_GUID") or "").strip()
            if not guid:
                logger.error("跳过没有 AMZ_CROSS_TASK_GUID 的 New 行")
                continue
            status = _claim_one(connection, store, table, row, guid, "UPDATED_DATE" in columns)
            if not status:
                continue
            claimed += 1
            if status == "成功":
                _report_claim(config, row, guid, "领取后本机已完成，直接回写 Confirm")
                try:
                    write_back(config, guid, "Confirm", "", "")
                except Exception:
                    logger.exception("已完成任务回写 Confirm 失败 AMZ_CROSS_TASK_GUID=%s", guid)
            else:
                _report_claim(config, row, guid, "领取，本机状态 " + status)
        return claimed
    finally:
        close_store(store)
        connection.close()


def _country_loop(code, config, database_path, picture_log_path=None, aplus_log_path=None):
    """先做完该国待重试和待处理，再领一条该国的 New。没有活就睡一个轮询间隔。"""
    interval = _interval(config)
    while True:
        worked = False
        try:
            from app.task_record import flush_pending_writebacks

            with _flush_lock:
                flush_pending_writebacks(config, database_path)
        except Exception:
            logger.exception("补回写 CL 失败 %s", code)
        try:
            from app.pipeline import PICTURE_ROOT
            from app.task_run import run_one_pending

            if run_one_pending(
                config,
                database_path,
                PICTURE_ROOT,
                log_path=picture_log_path,
                aplus_log_path=aplus_log_path,
                main_country=code,
            ):
                worked = True
        except Exception:
            logger.exception("执行本机任务失败 %s", code)
        if not worked:
            try:
                if claim_one(config, database_path, code):
                    worked = True
            except Exception:
                logger.exception("轮询 CL 任务失败 %s", code)
        if not worked:
            time.sleep(interval)


def claim_one(config, database_path, main_country):
    """领取该国最早的一条 New。更新行数是 1 才写入本机。没有新行时返回 0。"""
    code = str(main_country or "").strip().upper()
    name = dict(COUNTRY_THREADS).get(code, "")
    database = _require_db(config)
    table = _identifier(database.get("table"), "cl_db.table")
    connection = _connect(database)
    store = None
    try:
        columns = _table_columns(connection, table)
        missing = [item for item in READ_COLUMNS if item not in columns]
        if missing:
            raise RuntimeError("CL 表缺少列：" + "、".join(missing))
        if "MAIN_COUNTRY" not in columns:
            _warn_missing_country()
            return 0
        selected = [item for item in READ_COLUMNS if item in columns]
        selected.extend(item for item in OPTIONAL_COLUMNS if item in columns)
        row = _fetch_one_new(connection, table, selected, code, name)
        if row is None:
            return 0
        guid = str(row.get("AMZ_CROSS_TASK_GUID") or "").strip()
        if not guid:
            logger.error("跳过没有 AMZ_CROSS_TASK_GUID 的 New 行 %s", code)
            return 0
        store = open_store(database_path)
        status = _claim_one(connection, store, table, row, guid, "UPDATED_DATE" in columns)
        if not status:
            return 0
        logger.info("本轮领到 1 条 %s", code)
        if status == "成功":
            _report_claim(config, row, guid, "领取后本机已完成，直接回写 Confirm")
            try:
                write_back(config, guid, "Confirm", "", "")
            except Exception:
                logger.exception("已完成任务回写 Confirm 失败 AMZ_CROSS_TASK_GUID=%s", guid)
        else:
            _report_claim(config, row, guid, "领取，本机状态 " + status)
        return 1
    finally:
        close_store(store)
        connection.close()


def _warn_missing_country():
    global _warned_missing_country
    if _warned_missing_country:
        return
    _warned_missing_country = True
    logger.error("CL 表没有 MAIN_COUNTRY，五国线程不领取")


def _fetch_one_new(connection, table, columns, code, name):
    sql = (
        "SELECT " + ", ".join(columns)
        + " FROM " + table
        + " WHERE TASK_STATUS = %s AND (UPPER(MAIN_COUNTRY) = %s OR MAIN_COUNTRY = %s)"
        + " ORDER BY CREATION_DATE LIMIT 1"
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, ("New", code, name))
        return cursor.fetchone()


def reclaim_stale(config, exclude_guids=None):
    """把更新时间已早于设定秒数、仍为 Process 的行改回 New。正在执行的 GUID 不收回。"""
    database = _require_db(config)
    table = _identifier(database.get("table"), "cl_db.table")
    seconds = _stale_seconds(config)
    connection = _connect(database)
    try:
        if "UPDATED_DATE" not in _table_columns(connection, table):
            return []
        select_sql = (
            "SELECT AMZ_CROSS_TASK_GUID FROM " + table
            + " WHERE TASK_STATUS = %s"
            + " AND (UPDATED_DATE IS NULL OR UPDATED_DATE < DATE_SUB(NOW(), INTERVAL "
            + str(seconds)
            + " SECOND))"
        )
        with connection.cursor() as cursor:
            cursor.execute(select_sql, ("Process",))
            guids = [
                str(row.get("AMZ_CROSS_TASK_GUID") or "").strip()
                for row in cursor.fetchall()
            ]
        skip = {str(item or "").strip() for item in (exclude_guids or [])}
        guids = [guid for guid in guids if guid and guid not in skip]
        reclaimed = []
        update_sql = (
            "UPDATE " + table
            + " SET TASK_STATUS = %s, UPDATED_DATE = NOW()"
            + " WHERE AMZ_CROSS_TASK_GUID = %s AND TASK_STATUS = %s"
        )
        with connection.cursor() as cursor:
            for guid in guids:
                cursor.execute(update_sql, ("New", guid, "Process"))
                if cursor.rowcount == 1:
                    reclaimed.append(guid)
        if reclaimed:
            connection.commit()
        else:
            connection.rollback()
        return reclaimed
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _stale_seconds(config):
    """Process 超过这么久没刷新，就收回成 New。"""
    database = config.get("cl_db") if isinstance(config, dict) else None
    raw = database.get("stale_processing_seconds") if isinstance(database, dict) else None
    try:
        seconds = int(float(raw))
    except (TypeError, ValueError):
        seconds = 1800
    if seconds <= 0:
        return 1800
    return seconds


def _claim_one(connection, store, table, row, guid, touch_updated):
    try:
        if not _mark_process(connection, table, guid, touch_updated):
            connection.rollback()
            return ""
        status = save_claimed_task(store, row)
        connection.commit()
    except Exception:
        connection.rollback()
        logger.exception(
            "领取后写入本机 task 失败，已回滚 TASK_ID=%s AMZ_CROSS_TASK_GUID=%s",
            row.get("TASK_ID"),
            guid,
        )
        return ""
    from app.logging_setup import log_business

    log_business(
        logger,
        {
            "task_id": row.get("TASK_ID"),
            "amz_cross_task_guid": guid,
            "sku": row.get("SKU"),
            "asin": row.get("ASIN"),
            "cross_country": row.get("CROSS_COUNTRY"),
            "attempt": 0,
        },
        "claim",
        "已领取",
    )
    return status


def _report_claim(config, row, guid, text):
    from app.progress_feed import describe, report

    report(describe(config, {
        "task_id": row.get("TASK_ID"),
        "sku": row.get("SKU"),
        "amz_cross_task_guid": guid,
        "origin": "cl",
    }, text))


def list_new_tasks(config):
    """只读当前所有 New，给展示页用。不改 TASK_STATUS，不写入本机队列。

    五国线程仍各自领取一条。两边可以读到同一行，领取靠更新行数必须是 1，展示页不参与。
    """
    return list_followed_tasks(config, ())


def list_followed_tasks(config, guids):
    """只读当前 New，以及展示页已经在看的那些行的最新状态和错误。不改状态，不领取。"""
    database = _require_db(config)
    table = _identifier(database.get("table"), "cl_db.table")
    connection = _connect(database)
    try:
        columns = _table_columns(connection, table)
        missing = [name for name in READ_COLUMNS if name not in columns]
        if missing:
            raise RuntimeError("CL 表缺少列：" + "、".join(missing))
        selected = [name for name in READ_COLUMNS if name in columns]
        selected.extend(name for name in OPTIONAL_COLUMNS if name in columns)
        selected.extend(
            name
            for name in ("CREATION_DATE", "ERROR_CODE", "ERROR_MESSAGE")
            if name in columns and name not in selected
        )
        followed = []
        seen = set()
        for item in guids or []:
            text = str(item or "").strip()
            if text and text not in seen:
                seen.add(text)
                followed.append(text)
        return _fetch_followed(connection, table, selected, followed)
    finally:
        connection.close()


def _fetch_new(connection, table, columns):
    return _fetch_followed(connection, table, columns, ())


def _fetch_followed(connection, table, columns, guids):
    sql = (
        "SELECT " + ", ".join(columns)
        + " FROM " + table
        + " WHERE TASK_STATUS = %s"
    )
    params = ["New"]
    if guids:
        sql += " OR AMZ_CROSS_TASK_GUID IN (" + ", ".join(["%s"] * len(guids)) + ")"
        params.extend(guids)
    sql += " ORDER BY CREATION_DATE"
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return list(cursor.fetchall())


def _mark_process(connection, table, guid, touch_updated):
    assignment = "TASK_STATUS = %s"
    if touch_updated:
        assignment += ", UPDATED_DATE = NOW()"
    sql = (
        "UPDATE " + table
        + " SET " + assignment
        + " WHERE AMZ_CROSS_TASK_GUID = %s AND TASK_STATUS = %s"
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, ("Process", guid, "New"))
        return cursor.rowcount == 1


def _table_columns(connection, table):
    with connection.cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM " + table)
        return {row["Field"] for row in cursor.fetchall()}


def _connect(database, found_rows=False):
    """found_rows 时按匹配行数返回 rowcount。心跳刷新同一秒也算这一行还在。"""
    flags = CLIENT.FOUND_ROWS if found_rows else 0
    return pymysql.connect(
        host=database["host"],
        port=int(database.get("port") or 3306),
        user=database["user"],
        password=database["password"],
        database=database["database"],
        charset=database.get("charset") or "utf8mb4",
        connect_timeout=int(database.get("connect_timeout_seconds") or 10),
        read_timeout=int(database.get("connect_timeout_seconds") or 10),
        write_timeout=int(database.get("connect_timeout_seconds") or 10),
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
        client_flag=flags,
    )


def _require_db(config):
    database = config.get("cl_db") if isinstance(config, dict) else None
    if not isinstance(database, dict):
        raise ValueError("配置里没有 cl_db")
    missing = [
        name
        for name in ("host", "port", "database", "user", "password", "table")
        if not str(database.get(name) or "").strip()
    ]
    if missing:
        raise ValueError("cl_db 缺少：" + "、".join(missing))
    return database


def _interval(config):
    database = config.get("cl_db") if isinstance(config, dict) else None
    raw = database.get("poll_interval_seconds") if isinstance(database, dict) else None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        seconds = 30
    if seconds <= 0:
        return 30
    return seconds


def _identifier(value, label):
    text = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(text):
        raise ValueError(label + " 不是合法的表名")
    return text


def touch_updated(config, guid):
    """刷新仍为 Process 的这一行。没有 UPDATED_DATE 时返回 None，刷新到返回 1，行已不是 Process 返回 0。"""
    database = _require_db(config)
    table = _identifier(database.get("table"), "cl_db.table")
    connection = _connect(database, found_rows=True)
    try:
        if "UPDATED_DATE" not in _table_columns(connection, table):
            return None
        sql = (
            "UPDATE " + table
            + " SET UPDATED_DATE = NOW()"
            + " WHERE AMZ_CROSS_TASK_GUID = %s AND TASK_STATUS = %s"
        )
        with connection.cursor() as cursor:
            cursor.execute(sql, (str(guid or "").strip(), "Process"))
            count = cursor.rowcount
        if count == 1:
            connection.commit()
        else:
            connection.rollback()
        return 1 if count == 1 else 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def write_back(config, guid, status, error_code, error_message):
    """覆盖 CL 同一行的状态和错误，不新增行。

    只更新仍为 Process 的行。更新到 1 行才返回 True。
    """
    database = _require_db(config)
    table = _identifier(database.get("table"), "cl_db.table")
    connection = _connect(database)
    try:
        columns = _table_columns(connection, table)
        assignments = ["TASK_STATUS = %s", "ERROR_CODE = %s", "ERROR_MESSAGE = %s"]
        params = [status, str(error_code or ""), str(error_message or "")]
        if "UPDATED_DATE" in columns:
            assignments.append("UPDATED_DATE = NOW()")
        sql = (
            "UPDATE " + table
            + " SET " + ", ".join(assignments)
            + " WHERE AMZ_CROSS_TASK_GUID = %s AND TASK_STATUS = %s"
        )
        params.extend([str(guid or "").strip(), "Process"])
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            updated = cursor.rowcount == 1
        if updated:
            connection.commit()
        else:
            connection.rollback()
        return updated
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
