"""选择或拖入一个 Excel，并只把该文件写入队列。"""

from pathlib import Path
import zipfile

from openpyxl.utils.exceptions import InvalidFileException

from app.excel_io import read_workbook
from app.queue_store import close_store, enqueue, open_store


def choose_dropped_file(paths):
    """多个文件一律拒绝。只接受单独一个 .xlsx。

    返回 (路径, None)，或 (None, 给操作员看的原因)。
    """
    items = [Path(path) for path in paths]
    if not items:
        return None, "没有识别到拖入的文件。"
    if len(items) > 1:
        names = "、".join(item.name for item in items)
        return None, f"一次只能导入一个文件，本次拖入了 {len(items)} 个，已全部拒绝：{names}"
    path = items[0]
    if path.suffix.lower() != ".xlsx":
        return None, f"只能导入 .xlsx 文件，当前文件是：{path.name}"
    return path, None


def explain_read_failure(exc, path):
    """把读取失败转成可直接提示的原因。"""
    if isinstance(exc, ValueError):
        return str(exc)
    winerror = getattr(exc, "winerror", None)
    if winerror == 32 or "being used by another process" in str(exc).lower():
        return f"文件被占用，无法读取：{path}\n{exc}"
    if isinstance(exc, PermissionError) or winerror == 5:
        return f"没有读取权限：{path}\n{exc}"
    if isinstance(exc, (zipfile.BadZipFile, InvalidFileException)):
        return f"文件已损坏或不是有效的 Excel：{path}\n{exc}"
    if isinstance(exc, OSError):
        return f"无法读取文件：{path}\n{exc}"
    return f"无法读取文件：{path}\n{exc}"


def import_workbook(path, database_path):
    """读取一个工作簿并写入队列。返回导入行数。

    不打开浏览器，不回写 Excel。没有数据行时不写数据库。
    """
    rows = read_workbook(path)
    if not rows:
        return 0
    store = open_store(database_path)
    try:
        enqueue(store, rows)
    finally:
        close_store(store)
    return len(rows)
