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


def start_polling(config, database_path):
    """在后台按 poll_interval_seconds 领取，并执行一条待处理任务。"""
    from app.heartbeat import heartbeat

    heartbeat.start(config)
    thread = threading.Thread(
        target=_loop,
        args=(config, database_path),
        name="cl-task-poll",
        daemon=True,
    )
    thread.start()
    return thread


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
            if _claim_one(connection, store, table, row, guid, "UPDATED_DATE" in columns):
                claimed += 1
        return claimed
    finally:
        close_store(store)
        connection.close()


def _loop(config, database_path):
    interval = _interval(config)
    while True:
        try:
            from app.task_record import flush_pending_writebacks

            flush_pending_writebacks(config, database_path)
        except Exception:
            logger.exception("补回写 CL 失败")
        try:
            claimed = claim_available(config, database_path)
            if claimed:
                logger.info("本轮领到 %s 条", claimed)
        except Exception:
            logger.exception("轮询 CL 任务失败")
        try:
            from app.pipeline import PICTURE_ROOT
            from app.task_run import run_one_pending

            run_one_pending(config, database_path, PICTURE_ROOT)
        except Exception:
            logger.exception("执行本机任务失败")
        time.sleep(interval)


def _claim_one(connection, store, table, row, guid, touch_updated):
    try:
        if not _mark_process(connection, table, guid, touch_updated):
            connection.rollback()
            return False
        save_claimed_task(store, row)
        connection.commit()
    except Exception:
        connection.rollback()
        logger.exception(
            "领取后写入本机 task 失败，已回滚 TASK_ID=%s AMZ_CROSS_TASK_GUID=%s",
            row.get("TASK_ID"),
            guid,
        )
        return False
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
    return True


def _fetch_new(connection, table, columns):
    sql = (
        "SELECT " + ", ".join(columns)
        + " FROM " + table
        + " WHERE TASK_STATUS = %s ORDER BY CREATION_DATE"
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, ("New",))
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
