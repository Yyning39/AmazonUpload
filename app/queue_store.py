"""SQLite 队列。六列业务字段：sku、asin、country、picture、A+introduction、status。"""

from pathlib import Path
import sqlite3

from app.excel_io import BOOL_COLUMNS, EXCEL_COLUMNS

# Excel 没有这一列。新入队的记录使用该值，直到后续阶段改写。
INITIAL_STATUS = "待处理"
STATUS = "status"


def open_store(database_path):
    """打开或创建队列数据库，并确保队列表存在。"""
    if not str(database_path or "").strip():
        raise ValueError("未配置 database_file")
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
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
            picture INTEGER NOT NULL CHECK (picture IN (0, 1)),
            "A+introduction" INTEGER NOT NULL CHECK ("A+introduction" IN (0, 1)),
            status TEXT NOT NULL
        )
        """
    )
    connection.commit()
    return connection


def enqueue(store, rows):
    """把 Excel 的五列写入队列，并补上 status。

    不做去重。同一 sku 和 asin 再次导入会新增一行。
    picture 与 A+introduction 以 0/1 保存。
    """
    store.executemany(
        """
        INSERT INTO queue (sku, asin, country, picture, "A+introduction", status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["sku"],
                row["asin"],
                row["country"],
                int(row["picture"]),
                int(row["A+introduction"]),
                INITIAL_STATUS,
            )
            for row in rows
        ],
    )
    store.commit()


def list_queue(store):
    """按 id 返回全部队列记录。两个布尔列还原为 True/False。"""
    records = store.execute(
        'SELECT id, sku, asin, country, picture, "A+introduction", status FROM queue ORDER BY id'
    )
    items = []
    for record in records:
        item = {key: record[key] for key in ("id", *EXCEL_COLUMNS, STATUS)}
        for name in BOOL_COLUMNS:
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


def close_store(store):
    """关闭队列数据库。"""
    if store is not None:
        store.close()
