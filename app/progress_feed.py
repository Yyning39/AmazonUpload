"""把执行阶段送到展示页左侧进度。没有挂上窗口时只是不显示。"""

import threading
from datetime import datetime
from pathlib import Path

_lock = threading.Lock()
_aplus_log_lock = threading.Lock()
_listener = None


def set_listener(listener):
    """展示页启动后挂上。listener(text, done, total, refresh_records)。"""
    global _listener
    with _lock:
        _listener = listener


def report(text, done=0, total=0, refresh_records=False):
    """通知当前展示页。回调失败不影响任务。"""
    with _lock:
        listener = _listener
    if listener is None:
        return
    try:
        listener(text, done, total, refresh_records)
    except TypeError:
        listener(text, done, total)
    except Exception:
        return


def library_label(config):
    """按当前连接的库名区分测试库和正式库。"""
    database = ""
    if isinstance(config, dict):
        cl_db = config.get("cl_db")
        if isinstance(cl_db, dict):
            database = str(cl_db.get("database") or "").strip()
    if database == "catest":
        return "测试库"
    if database == "cadev":
        return "正式库"
    if database:
        return "数据库 " + database
    return "数据库"


def source_note(config):
    """窗口打开时说明这一进程会把哪些来源写进进度。"""
    label = library_label(config)
    database = ""
    if isinstance(config, dict):
        cl_db = config.get("cl_db")
        if isinstance(cl_db, dict):
            database = str(cl_db.get("database") or "").strip()
    current = label + ((" " + database) if database else "")
    if label == "测试库":
        other = "正式库未连接，本进程不领取。"
    elif label == "正式库":
        other = "测试库未连接，本进程不领取。"
    else:
        other = ""
    return f"当前连接：{current}。Excel 与该库的任务写在这里。{other}"


def describe(config, task, text):
    """一条进度：来源、任务号、SKU、阶段。"""
    task = task or {}
    guid = str(task.get("amz_cross_task_guid") or "")
    origin = str(task.get("origin") or "")
    if origin == "excel" or guid.startswith("excel:") or guid.startswith("excel-row:"):
        source = "Excel"
    else:
        source = library_label(config)
    parts = [f"[{source}]"]
    task_id = str(task.get("task_id") or "").strip()
    sku = str(task.get("sku") or "").strip()
    if task_id:
        parts.append("TASK_ID=" + task_id)
    if sku:
        parts.append("SKU=" + sku)
    parts.append(str(text or "").strip())
    return " ".join(parts)


def append_aplus_record(path, task, status, reason, code=""):
    """把这一条 A+ 结果追加到 aplus_log.md。路径为空则不写。"""
    if not path:
        return
    task = task or {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    block = "\n".join([
        f"【记录时间】：{now}",
        f"task_id：{task.get('task_id') or ''}",
        f"sku：{task.get('sku') or ''}",
        f"asin：{task.get('asin') or ''}",
        f"country：{task.get('main_country') or ''}",
        f"market：{task.get('cross_country') or ''}",
        f"结果：{status}",
        f"原因：{reason or ''}",
        f"错误代码：{code or ''}",
        f"错误详情：{reason or ''}",
        "",
    ])
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _aplus_log_lock:
        with target.open("a", encoding="utf-8") as handle:
            handle.write(block + "\n")
