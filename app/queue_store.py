"""SQLite 队列。Excel 用 queue；CL 领到的任务用 task。"""

from datetime import datetime
from pathlib import Path
import sqlite3

# 旧的 Excel 队列表。新的 Excel 任务写在 task，不再使用这些列。
QUEUE_COLUMNS = ("sku", "asin", "country", "market", "picture", "A+introduction", "src")
QUEUE_BOOL_COLUMNS = ("picture", "A+introduction")

# Excel 没有这一列。新入队的记录使用该值，直到后续阶段改写。
INITIAL_STATUS = "待处理"
STATUS = "status"


def open_store(database_path):
    """打开或创建队列数据库，并确保队列表存在。"""
    if not str(database_path or "").strip():
        raise ValueError("未配置 database_file")
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    columns = {row[1] for row in connection.execute("PRAGMA table_info(queue)")}
    if "sku/asin" in columns:
        connection.execute("DROP TABLE queue")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY,
            sku TEXT NOT NULL,
            asin TEXT NOT NULL,
            country TEXT NOT NULL,
            market TEXT NOT NULL,
            picture INTEGER NOT NULL CHECK (picture IN (0, 1)),
            "A+introduction" INTEGER NOT NULL CHECK ("A+introduction" IN (0, 1)),
            src TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL
        )
        """
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(queue)")}
    if "market" not in columns:
        connection.execute("ALTER TABLE queue ADD COLUMN market TEXT NOT NULL DEFAULT ''")
    if "src" not in columns:
        connection.execute("ALTER TABLE queue ADD COLUMN src TEXT NOT NULL DEFAULT ''")
    _ensure_task_table(connection)
    connection.commit()
    return connection


def enqueue(store, rows):
    """把 Excel 的列写入队列，并补上 status。

    不做去重。同一 sku 和 asin 再次导入会新增一行。
    picture 与 A+introduction 以 0/1 保存。
    """
    store.executemany(
        """
        INSERT INTO queue (sku, asin, country, market, picture, "A+introduction", src, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["sku"],
                row["asin"],
                row["country"],
                row["market"],
                int(row["picture"]),
                int(row["A+introduction"]),
                row["src"],
                INITIAL_STATUS,
            )
            for row in rows
        ],
    )
    store.commit()


def list_queue(store):
    """按 id 返回全部队列记录。两个布尔列还原为 True/False。"""
    records = store.execute(
        'SELECT id, sku, asin, country, market, picture, "A+introduction", src, status FROM queue ORDER BY id'
    )
    items = []
    for record in records:
        item = {key: record[key] for key in ("id", *QUEUE_COLUMNS, STATUS)}
        for name in QUEUE_BOOL_COLUMNS:
            item[name] = bool(item[name])
        items.append(item)
    return items


def update_status(store, item_id, status):
    """按 id 更新 status。不规定状态还有哪些取值。"""
    text = str(status or "").strip()
    if not text:
        raise ValueError("状态不能为空")
    cursor = store.execute("UPDATE queue SET status = ? WHERE id = ?", (text, item_id))
    if cursor.rowcount != 1:
        raise ValueError("队列中没有这条记录")
    store.commit()


def save_claimed_task(store, row):
    """把刚领到的 CL 行写入 task。返回写入后的本机状态。

    同一 TASK_ID 已存在时合并快照，保留 attempt 和分步状态。
    正在执行的行不改状态。站点图和 A+ 都已经成功或跳过时保持成功，不再排队。
    """
    guid = str(row.get("AMZ_CROSS_TASK_GUID") or "").strip()
    if not guid:
        raise ValueError("缺少 AMZ_CROSS_TASK_GUID")
    task_id = str(row.get("TASK_ID") or "").strip()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    snapshot = (
        guid,
        str(row.get("SKU") or ""),
        str(row.get("ASIN") or ""),
        str(row.get("MAIN_COUNTRY") or ""),
        str(row.get("CROSS_COUNTRY") or ""),
        str(row.get("ACTIONS") or ""),
        str(row.get("IMAGES") or ""),
    )
    existing = _find_task(store, task_id, guid)
    if existing is None:
        store.execute(
            """
            INSERT INTO task (
                task_id, amz_cross_task_guid, sku, asin, main_country, cross_country,
                actions, images, status, attempt, error_code, error_msg,
                site_image_status, aplus_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '', '', '未执行', '未执行', ?, ?)
            """,
            (task_id or None, *snapshot, "待处理", now, now),
        )
        store.commit()
        return "待处理"
    status = _status_after_claim(existing)
    error_code = str(existing["error_code"] or "")
    error_msg = str(existing["error_msg"] or "")
    if error_code == "CL_LOST":
        error_code = ""
        error_msg = ""
    if status == "成功":
        error_code = ""
        error_msg = ""
    store.execute(
        """
        UPDATE task
        SET amz_cross_task_guid = ?, sku = ?, asin = ?, main_country = ?, cross_country = ?,
            actions = ?, images = ?, status = ?, error_code = ?, error_msg = ?, updated_at = ?
        WHERE id = ?
        """,
        (*snapshot, status, error_code, error_msg, now, existing["id"]),
    )
    store.commit()
    return status


def _status_after_claim(existing):
    """再领到同一条时，已完成的不重新排队。"""
    if existing["status"] == "处理中":
        return "处理中"
    if _steps_finished(existing):
        return "成功"
    return "待处理"


def _steps_finished(existing):
    images_done = str(existing["site_image_status"] or "") == "成功"
    aplus_done = str(existing["aplus_status"] or "") in {"成功", "跳过"}
    return images_done and aplus_done


def _ensure_task_table(store):
    store.execute(
        """
        CREATE TABLE IF NOT EXISTS task (
            id INTEGER PRIMARY KEY,
            task_id TEXT,
            amz_cross_task_guid TEXT NOT NULL,
            sku TEXT NOT NULL DEFAULT '',
            asin TEXT NOT NULL DEFAULT '',
            main_country TEXT NOT NULL DEFAULT '',
            cross_country TEXT NOT NULL DEFAULT '',
            actions TEXT NOT NULL DEFAULT '',
            images TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            attempt INTEGER NOT NULL DEFAULT 0,
            error_code TEXT NOT NULL DEFAULT '',
            error_msg TEXT NOT NULL DEFAULT '',
            site_image_status TEXT NOT NULL DEFAULT '未执行',
            aplus_status TEXT NOT NULL DEFAULT '未执行',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    store.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS task_task_id
        ON task(task_id) WHERE task_id IS NOT NULL AND task_id <> ''
        """
    )
    store.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS task_guid
        ON task(amz_cross_task_guid)
        """
    )
    columns = {row[1] for row in store.execute("PRAGMA table_info(task)")}
    if "cl_pending" not in columns:
        store.execute("ALTER TABLE task ADD COLUMN cl_pending INTEGER NOT NULL DEFAULT 0")
    if "origin" not in columns:
        store.execute("ALTER TABLE task ADD COLUMN origin TEXT NOT NULL DEFAULT 'cl'")
    if "excel_path" not in columns:
        store.execute("ALTER TABLE task ADD COLUMN excel_path TEXT NOT NULL DEFAULT ''")
    if "excel_row" not in columns:
        store.execute("ALTER TABLE task ADD COLUMN excel_row INTEGER NOT NULL DEFAULT 0")
    store.execute(
        """
        CREATE TABLE IF NOT EXISTS task_attempt (
            id INTEGER PRIMARY KEY,
            task_id TEXT,
            attempt INTEGER NOT NULL,
            action TEXT NOT NULL,
            error_code TEXT NOT NULL DEFAULT '',
            error_msg TEXT NOT NULL DEFAULT '',
            detail TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )


def release_processing_tasks(store):
    """上次进程退出时还停在处理中的任务，改为待重试。不改次数，不回写 CL。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = store.execute(
        """
        UPDATE task
        SET status = '待重试', updated_at = ?
        WHERE status = '处理中'
        """,
        (now,),
    )
    store.commit()
    return cursor.rowcount


_COUNTRY_ALIASES = {
    "UK": ("UK", "英国"),
    "DE": ("DE", "德国"),
    "FR": ("FR", "法国"),
    "IT": ("IT", "意大利"),
    "ES": ("ES", "西班牙"),
}


def take_pending_task(store, main_country=None):
    """没有该国处理中的任务时，优先取待重试，再取待处理，改为处理中并返回。

    传入 main_country 时只看这一国。不传时仍看全部，并且全库只能有一条处理中。
    """
    store.execute("BEGIN IMMEDIATE")
    code = str(main_country or "").strip().upper()
    aliases = tuple(item.upper() for item in _COUNTRY_ALIASES.get(code, (code,))) if code else ()
    if code:
        marks = ", ".join("?" for _ in aliases)
        busy = store.execute(
            f"SELECT id FROM task WHERE status = '处理中' AND upper(trim(main_country)) IN ({marks}) LIMIT 1",
            aliases,
        ).fetchone()
    else:
        busy = store.execute("SELECT id FROM task WHERE status = '处理中' LIMIT 1").fetchone()
    if busy is not None:
        store.rollback()
        return None
    if code:
        marks = ", ".join("?" for _ in aliases)
        row = store.execute(
            f"""
            SELECT * FROM task
            WHERE status IN ('待处理', '待重试') AND COALESCE(origin, 'cl') = 'cl'
              AND COALESCE(error_code, '') <> 'CL_LOST'
              AND upper(trim(main_country)) IN ({marks})
            ORDER BY CASE status WHEN '待重试' THEN 0 ELSE 1 END, id
            LIMIT 1
            """,
            aliases,
        ).fetchone()
    else:
        row = store.execute(
            """
            SELECT * FROM task
            WHERE status IN ('待处理', '待重试') AND COALESCE(origin, 'cl') = 'cl'
              AND COALESCE(error_code, '') <> 'CL_LOST'
            ORDER BY id
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        store.rollback()
        return None
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = store.execute(
        "UPDATE task SET status = '处理中', updated_at = ? WHERE id = ? AND status IN ('待处理', '待重试')",
        (now, row["id"]),
    )
    if cursor.rowcount != 1:
        store.rollback()
        return None
    store.commit()
    return {key: row[key] for key in row.keys()}


def save_excel_task(store, row):
    """把 Excel 的一行写入 task。同一 TASK_ID 已有 CL 任务时拒绝覆盖。

    表里状态为空当作待处理。上次中断留下的处理中改为待重试。
    """
    task_id = str(row.get("task_id") or "").strip()
    guid = "excel:" + task_id if task_id else "excel-row:" + str(row.get("excel_row") or "")
    existing = None
    if task_id:
        existing = store.execute("SELECT * FROM task WHERE task_id = ?", (task_id,)).fetchone()
    if existing is None:
        existing = store.execute("SELECT * FROM task WHERE amz_cross_task_guid = ?", (guid,)).fetchone()
    if existing is not None and str(existing["origin"] or "cl") != "excel":
        raise ValueError(f"TASK_ID {task_id} 已是数据库任务，不能用 Excel 覆盖")
    status = str(row.get("status") or "").strip() or "待处理"
    if status == "处理中":
        status = "待重试"
    attempt = _attempt(row.get("attempt"))
    site_image_status = str(row.get("site_image_status") or "").strip() or "未执行"
    aplus_status = str(row.get("aplus_status") or "").strip() or "未执行"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    values = (
        task_id or None,
        guid,
        str(row.get("sku") or ""),
        str(row.get("asin") or ""),
        str(row.get("main_country") or ""),
        str(row.get("cross_country") or ""),
        str(row.get("actions") or ""),
        str(row.get("images") or ""),
        status,
        attempt,
        str(row.get("error_code") or ""),
        str(row.get("error_msg") or ""),
        site_image_status,
        aplus_status,
        str(row.get("excel_path") or ""),
        int(row.get("excel_row") or 0),
        now,
    )
    if existing is None:
        store.execute(
            """
            INSERT INTO task (
                task_id, amz_cross_task_guid, sku, asin, main_country, cross_country,
                actions, images, status, attempt, error_code, error_msg,
                site_image_status, aplus_status, excel_path, excel_row, origin,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'excel', ?, ?)
            """,
            (*values, now),
        )
    else:
        store.execute(
            """
            UPDATE task
            SET task_id = ?, amz_cross_task_guid = ?, sku = ?, asin = ?, main_country = ?,
                cross_country = ?, actions = ?, images = ?, status = ?, attempt = ?,
                error_code = ?, error_msg = ?, site_image_status = ?, aplus_status = ?,
                excel_path = ?, excel_row = ?, origin = 'excel', updated_at = ?
            WHERE id = ?
            """,
            (*values, existing["id"]),
        )
    store.commit()
    found = store.execute("SELECT * FROM task WHERE amz_cross_task_guid = ?", (guid,)).fetchone()
    return {key: found[key] for key in found.keys()}


def set_task_status(store, task_pk, status):
    """只改本机状态。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    store.execute(
        "UPDATE task SET status = ?, updated_at = ? WHERE id = ?",
        (status, now, task_pk),
    )
    store.commit()


def load_task(store, task_pk):
    """按本机主键读取 task。"""
    found = store.execute("SELECT * FROM task WHERE id = ?", (task_pk,)).fetchone()
    if found is None:
        raise ValueError("本机任务不存在")
    return {key: found[key] for key in found.keys()}


def _attempt(value):
    try:
        count = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return count if count > 0 else 0


def mark_task_retry(store, task_pk, error_msg):
    """切站点失败时改为待重试，并记下原因。不改 attempt。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    store.execute(
        """
        UPDATE task
        SET status = '待重试', error_msg = ?, updated_at = ?
        WHERE id = ?
        """,
        (str(error_msg or "")[:500], now, task_pk),
    )
    store.commit()


def _find_task(store, task_id, guid):
    columns = "id, status, error_code, error_msg, site_image_status, aplus_status"
    if task_id:
        found = store.execute(
            "SELECT " + columns + " FROM task WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if found is not None:
            return found
    return store.execute(
        "SELECT " + columns + " FROM task WHERE amz_cross_task_guid = ?",
        (guid,),
    ).fetchone()


def close_store(store):
    """关闭队列数据库。"""
    if store is not None:
        store.close()
