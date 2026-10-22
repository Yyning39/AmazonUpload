"""把一次结果记到本机。本轮结束或无法继续时，按两边的最终结果回写 CL 同一行。"""

import logging
from datetime import datetime

from app.cl_task import write_back
from app.queue_store import close_store, open_store

logger = logging.getLogger(__name__)

RETRYABLE_CODES = {
    "BROWSER_CONNECT_FAILED",
    "PAGE_NOT_READY",
    "IMG_DOWNLOAD_FAILED",
    "IMG_SEARCH_TIMEOUT",
    "IMG_SLOT_FAILED",
    "APLUS_SEARCH_TIMEOUT",
    "APLUS_SUBMIT_FAILED",
    "UNKNOWN",
}
TERMINAL_CODES = {
    "TASK_ID_EMPTY",
    "COUNTRY_INVALID",
    "SKU_ASIN_EMPTY",
    "IMG_EMPTY",
    "IMG_ITEM_EMPTY",
    "IMG_URL_INVALID",
    "ASIN_EMPTY",
    "IMG_SEARCH_NOT_UNIQUE",
    "IMG_TYPE_UNSUPPORTED",
    "APLUS_ASIN_MISMATCH",
}


def record_enqueue_rejection(config, database_path, task, code, message, writer=None):
    """入队不合格：本机失败，CL 写成 Error。attempt 不加。"""
    current = _load(database_path, task["id"])
    return _record(
        config,
        database_path,
        current,
        code=code,
        message=message,
        action="enqueue",
        detail="",
        attempt=None,
        status="失败",
        site_image_status=None,
        aplus_status=None,
        cl_status="Error",
        clear_error=False,
        writer=writer,
    )


def begin_attempt(database_path, task):
    """一次真正执行开始。已执行次数加一。入队校验失败不加。"""
    current = _load(database_path, task["id"])
    attempt = int(current["attempt"] or 0) + 1
    store = open_store(database_path)
    try:
        store.execute(
            "UPDATE task SET attempt = ?, updated_at = ? WHERE id = ?",
            (attempt, _now(), task["id"]),
        )
        store.commit()
    finally:
        close_store(store)
    task["attempt"] = attempt
    return attempt


def record_execution_failure(config, database_path, task, code, message, action, detail="", writer=None):
    """执行失败。可重试且未到上限时本机待重试、CL 保持 Process；否则本机失败、CL 写成 Error。"""
    current = _load(database_path, task["id"])
    attempt = int(current["attempt"] or 0)
    if attempt < 1:
        attempt = 1
    terminal = code not in RETRYABLE_CODES or attempt >= _max_attempts(config)
    step = "失败"
    return _record(
        config,
        database_path,
        current,
        code=code,
        message=message,
        action=action,
        detail=detail,
        attempt=attempt,
        status="失败" if terminal else "待重试",
        site_image_status=step if action == "site_images" else None,
        aplus_status=step if action == "aplus" else None,
        cl_status="Error" if terminal else "Process",
        clear_error=False,
        writer=writer,
    )


def record_aplus_skip(config, database_path, task, detail="无草稿", writer=None):
    """没有草稿。不写 CL 错误。站点图已成功时把同一行写成 Confirm。"""
    current = _load(database_path, task["id"])
    confirm = current["site_image_status"] == "成功"
    return _record(
        config,
        database_path,
        current,
        code="",
        message="",
        action="aplus",
        detail=detail,
        attempt=None,
        status="成功" if confirm else current["status"],
        site_image_status=None,
        aplus_status="跳过",
        cl_status="Confirm" if confirm else "",
        clear_error=confirm,
        writer=writer,
    )


def record_success(config, database_path, task, writer=None, aplus_status=None):
    """整单成功。CL 同一行写成 Confirm，并清空错误。

    没有传入 A+ 状态、且这一步已经是跳过时，保留跳过。真正提交成功时传入成功。
    """
    current = _load(database_path, task["id"])
    if aplus_status is None:
        aplus_status = "跳过" if current["aplus_status"] == "跳过" else "成功"
    return _record(
        config,
        database_path,
        current,
        code="",
        message="",
        action="",
        detail="",
        attempt=None,
        status="成功",
        site_image_status="成功",
        aplus_status=aplus_status,
        cl_status="Confirm",
        clear_error=True,
        writer=writer,
        append_attempt=False,
    )


def record_step_success(database_path, task, action):
    """这一步成功时只改本机。等这一轮结束再按两边的最终结果回写 CL。"""
    if action == "site_images":
        column = "site_image_status"
    elif action == "aplus":
        column = "aplus_status"
    else:
        raise ValueError("未知步骤")
    store = open_store(database_path)
    try:
        store.execute(
            "UPDATE task SET " + column + " = '成功', updated_at = ? WHERE id = ?",
            (_now(), task["id"]),
        )
        store.commit()
    finally:
        close_store(store)


def record_login_pause(database_path, country):
    """该主站 Chrome 登录不可用。相关任务改为待人工，不回写 CL，不计次数。"""
    code = str(country or "").strip().upper()
    if not code:
        return 0
    now = _now()
    store = open_store(database_path)
    try:
        cursor = store.execute(
            """
            UPDATE task
            SET status = '待人工', updated_at = ?
            WHERE upper(main_country) = ? AND status IN ('待处理', '处理中', '待重试')
            """,
            (now, code),
        )
        store.commit()
        count = cursor.rowcount
    finally:
        close_store(store)
    logger.info("登录暂停 main_country=%s 任务数=%s", code, count)
    return count


def flush_pending_writebacks(config, database_path, writer=None):
    """只补上次没写成的 CL 回写，不新增 task_attempt。"""
    writer = write_back if writer is None else writer
    store = open_store(database_path)
    try:
        rows = list(
            store.execute(
                "SELECT * FROM task WHERE cl_pending = 1 AND COALESCE(origin, 'cl') = 'cl'"
            )
        )
    finally:
        close_store(store)
    done = 0
    for row in rows:
        task = {key: row[key] for key in row.keys()}
        if _push_cl(config, database_path, task, writer):
            done += 1
    return done


def _record(
    config,
    database_path,
    task,
    code,
    message,
    action,
    detail,
    attempt,
    status,
    site_image_status,
    aplus_status,
    cl_status,
    clear_error,
    writer,
    append_attempt=True,
):
    writer = write_back if writer is None else writer
    now = _now()
    saved_attempt = int(task["attempt"] or 0) if attempt is None else int(attempt)
    saved_code = "" if clear_error else str(code or "")
    saved_message = "" if clear_error else str(message or "")
    store = open_store(database_path)
    try:
        store.execute("BEGIN IMMEDIATE")
        store.execute(
            """
            UPDATE task
            SET status = ?, attempt = ?, error_code = ?, error_msg = ?,
                site_image_status = COALESCE(?, site_image_status),
                aplus_status = COALESCE(?, aplus_status),
                cl_pending = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                saved_attempt,
                saved_code,
                saved_message,
                site_image_status,
                aplus_status,
                1 if cl_status else 0,
                now,
                task["id"],
            ),
        )
        if append_attempt:
            store.execute(
                """
                INSERT INTO task_attempt (
                    task_id, attempt, action, error_code, error_msg, detail, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.get("task_id"),
                    saved_attempt,
                    action,
                    str(code or ""),
                    str(message or ""),
                    str(detail or ""),
                    now,
                ),
            )
        store.commit()
    except Exception:
        store.rollback()
        raise
    finally:
        close_store(store)
    current = _load(database_path, task["id"])
    _log_result(current, action, saved_code)
    if cl_status:
        _push_cl(config, database_path, current, writer)
    return current


def _push_cl(config, database_path, task, writer):
    status = task["status"]
    if status in {"失败", "待重试"}:
        cl_status = "Error" if status == "失败" else "Process"
        code, message = _combined_error(database_path, task)
    elif status == "成功":
        cl_status, code, message = "Confirm", "", ""
    else:
        return False
    try:
        updated = writer(config, task["amz_cross_task_guid"], cl_status, code, message)
    except Exception:
        logger.exception(
            "回写 CL 失败 TASK_ID=%s AMZ_CROSS_TASK_GUID=%s",
            task.get("task_id"),
            task.get("amz_cross_task_guid"),
        )
        return False
    if not updated:
        logger.error(
            "回写 CL 未更新到该行 TASK_ID=%s AMZ_CROSS_TASK_GUID=%s",
            task.get("task_id"),
            task.get("amz_cross_task_guid"),
        )
        return False
    store = open_store(database_path)
    try:
        store.execute("UPDATE task SET cl_pending = 0, updated_at = ? WHERE id = ?", (_now(), task["id"]))
        store.commit()
    finally:
        close_store(store)
    return True


def _combined_error(database_path, task):
    """按图片和 A+ 的最终状态组成一次回写。只有一边失败时不带另一边。"""
    image = _latest_step_error(database_path, task, "site_images") if task["site_image_status"] == "失败" else None
    aplus = _latest_step_error(database_path, task, "aplus") if task["aplus_status"] == "失败" else None
    if image and aplus:
        return image[0] + ";" + aplus[0], "图片：" + image[1] + "；A+：" + aplus[1]
    if image:
        return image
    if aplus:
        return aplus
    return str(task.get("error_code") or ""), str(task.get("error_msg") or "")


def _latest_step_error(database_path, task, action):
    store = open_store(database_path)
    try:
        if task.get("task_id"):
            row = store.execute(
                """
                SELECT error_code, error_msg FROM task_attempt
                WHERE task_id = ? AND action = ? AND error_code <> ''
                ORDER BY id DESC LIMIT 1
                """,
                (task.get("task_id"), action),
            ).fetchone()
        else:
            row = None
    finally:
        close_store(store)
    if row is None:
        return None
    return str(row["error_code"] or ""), str(row["error_msg"] or "")


def _load(database_path, task_pk):
    store = open_store(database_path)
    try:
        row = store.execute("SELECT * FROM task WHERE id = ?", (task_pk,)).fetchone()
    finally:
        close_store(store)
    if row is None:
        raise ValueError("本机任务不存在")
    return {key: row[key] for key in row.keys()}


def _log_result(task, action, code):
    logger.info(
        "记录结果 TASK_ID=%s AMZ_CROSS_TASK_GUID=%s sku=%s asin=%s cross_country=%s action=%s attempt=%s error_code=%s status=%s",
        task.get("task_id"),
        task.get("amz_cross_task_guid"),
        task.get("sku"),
        task.get("asin"),
        task.get("cross_country"),
        action,
        task.get("attempt"),
        code,
        task.get("status"),
    )


def _max_attempts(config):
    try:
        count = int((config or {}).get("max_attempts"))
    except (TypeError, ValueError):
        return 3
    return count if count > 0 else 3


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
