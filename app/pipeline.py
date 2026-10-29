"""Excel 读入后走和数据库任务相同的执行，并把状态写回 Excel。"""

import logging
from pathlib import Path

from app.config_loader import bundle_dir, load_config
from app.excel_io import read_workbook, write_task_row
from app.logging_setup import log_business
from app.picture_save import parse_image_addresses
from app.progress_feed import describe
from app.queue_store import close_store, load_task, open_store, save_excel_task, set_task_status
from app.task_check import check_excel
from app.task_record import record_enqueue_rejection
from app.task_run import run_prepared

# 图片目录：D:\aosom\data\picture\<国家代码>\<sku>\<站点代码>
PICTURE_ROOT = Path(r"D:\aosom\data\picture")
_FINISHED = {"成功", "失败", "待人工"}


def run(excel_path, database_path, log_path, picture_root=None, timeout=30, on_progress=None, config=None, aplus_log_path=None):
    """窗口选中的 Excel 读入、执行，并把状态写回这张表。"""
    if config is None:
        config = load_config(bundle_dir() / "config" / "settings.json")
    return upload_from_excel(
        excel_path,
        picture_root or PICTURE_ROOT,
        database_path,
        log_path,
        timeout,
        on_progress,
        config,
        aplus_log_path,
    )


def upload_from_excel(excel_path, picture_root, database_path, log_path=None, timeout=30, on_progress=None, config=None, aplus_log_path=None):
    """按表中的状态执行。成功、失败、待人工不再跑。待重试会接着做，图片已成功则只做 A+。"""
    _notify(on_progress, describe(config, {"origin": "excel"}, "正在读取 Excel"), 0, 0)
    rows = read_workbook(excel_path)
    store = open_store(database_path)
    writer = _excel_writer(database_path)
    results = []
    seen = set()
    total = len(rows)
    try:
        _notify(on_progress, describe(config, {"origin": "excel"}, f"共 {total} 行"), 0, total)
        for index, row in enumerate(rows, 1):
            task_id = str(row.get("task_id") or "").strip()
            if task_id and task_id in seen:
                _write_duplicate(excel_path, row)
                results.append({"task_id": task_id, "status": "失败", "reason": "同一张表里 TASK_ID 重复"})
                _notify(on_progress, describe(config, {"origin": "excel", "task_id": task_id}, f"第 {index}/{total} 行 TASK_ID 重复"), index, total)
                continue
            if task_id:
                seen.add(task_id)
            task = save_excel_task(store, row)
            if task["status"] in _FINISHED:
                results.append({"task_id": task.get("task_id"), "status": task["status"], "reason": "已结束，未再执行"})
                _notify(on_progress, describe(config, task, f"第 {index}/{total} 行 已是{task['status']}，未再执行"), index, total)
                continue
            addresses = parse_image_addresses(row.get("images"))
            problem = check_excel(task, addresses)
            if problem is not None:
                code, message = problem
                log_business(logging.getLogger(__name__), task, "enqueue", "校验失败 " + code + " " + message)
                record_enqueue_rejection(config, database_path, task, code, message, writer=writer)
                current = load_task(store, task["id"])
                write_task_row(excel_path, current["excel_row"], current)
                results.append({"task_id": current.get("task_id"), "status": current["status"], "reason": message})
                _notify(on_progress, describe(config, task, f"第 {index}/{total} 行 校验失败：{message}"), index, total)
                continue
            set_task_status(store, task["id"], "处理中")
            task = load_task(store, task["id"])
            write_task_row(excel_path, task["excel_row"], task)
            _notify(on_progress, describe(config, task, f"第 {index}/{total} 行 开始执行"), index, total)
            run_prepared(
                config,
                database_path,
                picture_root,
                task,
                timeout,
                log_path,
                writer=writer,
                addresses=addresses,
                on_progress=on_progress,
                aplus_log_path=aplus_log_path,
            )
            current = load_task(store, task["id"])
            write_task_row(excel_path, current["excel_row"], current)
            results.append({
                "task_id": current.get("task_id"),
                "status": current["status"],
                "reason": current.get("error_msg") or "",
            })
            ending = current["status"]
            if current.get("error_msg"):
                ending = ending + "：" + str(current.get("error_msg"))
            _notify(on_progress, describe(config, current, f"第 {index}/{total} 行 结束 {ending}"), index, total)
        _notify(on_progress, describe(config, {"origin": "excel"}, "全部处理完"), total or 1, total or 1)
    finally:
        close_store(store)
    return {"excel": rows, "results": results}


def _excel_writer(database_path):
    """状态已经写入本机 task 之后，按行号写回 Excel。不写 CL。"""

    def writer(config, guid, status, error_code, error_message):
        del config, status, error_code, error_message
        store = open_store(database_path)
        try:
            found = store.execute(
                "SELECT * FROM task WHERE amz_cross_task_guid = ?",
                (guid,),
            ).fetchone()
        finally:
            close_store(store)
        if found is None:
            return False
        task = {key: found[key] for key in found.keys()}
        write_task_row(task["excel_path"], task["excel_row"], task)
        return True

    return writer


def _write_duplicate(excel_path, row):
    write_task_row(
        excel_path,
        row["excel_row"],
        {
            "status": "失败",
            "attempt": row.get("attempt") or 0,
            "error_code": "",
            "error_msg": "同一张表里 TASK_ID 重复",
            "site_image_status": row.get("site_image_status") or "未执行",
            "aplus_status": row.get("aplus_status") or "未执行",
        },
    )


def _notify(on_progress, text, done, total):
    if on_progress is not None:
        on_progress(text, done, total)
