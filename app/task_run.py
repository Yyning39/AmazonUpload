"""按本机 task 传站点图并提交 A+。Excel 的 queue 不走这里。"""

import logging

from app.aplus_upload import (
    AplusAsinMismatch,
    AplusNotFound,
    open_matched_description,
    search_description,
    submit_for_approval,
)
from app.browser import (
    SITE_OPTIONS,
    BrowserConnectionError,
    PageNavigationError,
    close_browser,
    connect_country,
    country_code,
    return_to_imaging_manage,
    switch_site,
    switch_to_description_page,
)
from app.heartbeat import CL_LOST, ClaimLost, heartbeat
from app.imaging_upload import NeedsManual, upload_images
from app.logging_setup import log_business
from app.picture_save import PictureSourceError, image_src_list, picture_directory, save_address_pictures, save_task_pictures
from app.progress_feed import append_aplus_record, describe, report
from app.queue_store import close_store, open_store, take_pending_task
from app.task_check import check_enqueue
from app.task_record import (
    begin_attempt,
    record_aplus_skip,
    record_enqueue_rejection,
    record_execution_failure,
    record_step_success,
    record_success,
)

logger = logging.getLogger(__name__)

_SOURCE_CODES = {
    "TASK_ID_EMPTY",
    "COUNTRY_INVALID",
    "SKU_ASIN_EMPTY",
    "IMG_EMPTY",
    "IMG_ITEM_EMPTY",
    "IMG_URL_INVALID",
    "ASIN_EMPTY",
}


def run_one_pending(config, database_path, picture_root, timeout=30, log_path=None, on_progress=None, aplus_log_path=None, main_country=None):
    """领取一条待处理或待重试，传图后再做 A+。没有可跑的任务时返回 False。

    main_country 有值时只跑这一国，不影响其他国家正在执行的任务。
    """
    store = open_store(database_path)
    try:
        task = take_pending_task(store, main_country)
    finally:
        close_store(store)
    if task is None:
        return False
    run_prepared(
        config,
        database_path,
        picture_root,
        task,
        timeout,
        log_path,
        on_progress=on_progress,
        aplus_log_path=aplus_log_path,
    )
    return True


def run_prepared(
    config,
    database_path,
    picture_root,
    task,
    timeout=30,
    log_path=None,
    writer=None,
    addresses=None,
    on_progress=None,
    aplus_log_path=None,
):
    """执行已经读入的一条任务。addresses 有值时按这串地址下载，不再解析 JSON。"""
    task = dict(task)
    if addresses is not None:
        task["_addresses"] = list(addresses)
    step = "site_images" if task.get("site_image_status") != "成功" else "aplus"
    try:
        return _run_task(
            config,
            database_path,
            picture_root,
            task,
            timeout,
            log_path,
            writer,
            on_progress,
            aplus_log_path,
        )
    except ClaimLost:
        _hold_lost(database_path, task)
        _stage(on_progress, config, task, "CL 行已超过更新时限，已停止", done=1, total=1)
        return step
    except Exception as exc:
        logger.exception(
            "任务未记入结果 TASK_ID=%s AMZ_CROSS_TASK_GUID=%s",
            task.get("task_id"),
            task.get("amz_cross_task_guid"),
        )
        _fail(config, database_path, task, "UNKNOWN", str(exc), step, writer, on_progress)
        return step


def _run_task(config, database_path, picture_root, task, timeout, log_path, writer=None, on_progress=None, aplus_log_path=None):
    heartbeat.begin(config, task)
    try:
        return _run_task_body(
            config,
            database_path,
            picture_root,
            task,
            timeout,
            log_path,
            writer,
            on_progress,
            aplus_log_path,
        )
    finally:
        heartbeat.end(task)


def _run_task_body(config, database_path, picture_root, task, timeout, log_path, writer=None, on_progress=None, aplus_log_path=None):
    if task.get("_addresses") is None:
        problem = check_enqueue(task)
        if problem is not None:
            code, message = problem
            log_business(logger, task, "enqueue", "校验失败 " + code + " " + message)
            _release_heartbeat(task)
            record_enqueue_rejection(config, database_path, task, code, message, writer=writer)
            _stage(on_progress, config, task, f"校验失败：{code} {message}", done=1, total=1)
            return "enqueue"
        log_business(logger, task, "enqueue", "校验通过")
        _stage(on_progress, config, task, "校验通过")
    need_images = task.get("site_image_status") != "成功"
    need_aplus = str(task.get("aplus_status") or "") not in {"成功", "跳过"}
    if not need_images and not need_aplus:
        _release_heartbeat(task)
        record_success(config, database_path, task, writer=writer)
        _stage(on_progress, config, task, "站点图和 A+ 都已完成，回写确认", done=1, total=1)
        return "aplus"
    _beat(task)
    main_country, cross_country = _countries(config, database_path, task, writer, on_progress)
    if not main_country:
        return "site_images" if need_images else "aplus"
    begin_attempt(database_path, task)
    _stage(on_progress, config, task, f"第 {task.get('attempt')} 次执行")
    if need_images:
        _beat(task)
        _stage(on_progress, config, task, "进入下载")
        folder = _download_images(config, database_path, picture_root, task, writer, on_progress)
        if folder is None:
            return "site_images"
    else:
        folder = None
        _stage(on_progress, config, task, "站点图已成功，跳过上传")
    session = None
    try:
        _beat(task)
        _stage(on_progress, config, task, f"连接主站 {main_country}")
        session = connect_country(main_country, timeout=timeout)
        _stage(on_progress, config, task, f"切换站点 {cross_country}")
        switch_site(session, cross_country, timeout=timeout)
        if need_images:
            _stage(on_progress, config, task, "进入上传")
            _upload(session, task, main_country, cross_country, folder, timeout, log_path, on_progress, config)
            _beat(task)
            record_step_success(database_path, task, "site_images")
            task["site_image_status"] = "成功"
            _mirror(writer, task)
            _stage(on_progress, config, task, "站点图成功", refresh=True)
        if need_aplus:
            _beat(task)
            _stage(on_progress, config, task, "进入 A+")
            _submit_aplus(session, task, main_country, timeout)
        else:
            _stage(on_progress, config, task, "A+ 已完成，跳过")
    except AplusNotFound as exc:
        _release_heartbeat(task)
        record_aplus_skip(config, database_path, task, str(exc), writer=writer)
        append_aplus_record(aplus_log_path, task, "跳过", str(exc))
        _stage(on_progress, config, task, "A+ 跳过：" + str(exc), done=1, total=1, refresh=True)
        return "aplus"
    except AplusAsinMismatch as exc:
        _release_heartbeat(task)
        _fail(config, database_path, task, "APLUS_ASIN_MISMATCH", str(exc), "aplus", writer, on_progress)
        append_aplus_record(aplus_log_path, task, "失败", str(exc), code="APLUS_ASIN_MISMATCH")
        return "aplus"
    except ClaimLost:
        raise
    except Exception as exc:
        _release_heartbeat(task)
        action = "site_images" if need_images and task.get("site_image_status") != "成功" else "aplus"
        code, message = _failure_code(exc, action)
        _fail(config, database_path, task, code, message, action, writer, on_progress)
        if action == "aplus":
            append_aplus_record(aplus_log_path, task, "失败", message)
        return action
    else:
        _release_heartbeat(task)
        record_success(
            config,
            database_path,
            task,
            aplus_status="成功" if need_aplus else None,
            writer=writer,
        )
        if need_aplus:
            append_aplus_record(aplus_log_path, task, "成功", "")
            _stage(on_progress, config, task, "A+ 成功", done=1, total=1, refresh=True)
        else:
            _stage(on_progress, config, task, "完成：成功", done=1, total=1, refresh=True)
        return "aplus"
    finally:
        try:
            close_browser(session)
        except Exception:
            logger.exception("关闭浏览器连接失败")


def _beat(task):
    """还持有这一行才继续。终态回写之前先停心跳。"""
    heartbeat.raise_if_lost(task)


def _release_heartbeat(task):
    heartbeat.raise_if_lost(task)
    heartbeat.end(task)


def _mirror(writer, task):
    """图片这一步先只改了本机。Excel 任务要立刻把这一步写回表格。"""
    if writer is None:
        return
    writer(None, task.get("amz_cross_task_guid"), "", "", "")


def _download_images(config, database_path, picture_root, task, writer=None, on_progress=None):
    """下载失败时已经记过结果，返回 None。成功返回目录。"""
    sku = str(task.get("sku") or "").strip()
    asin = str(task.get("asin") or "").strip()
    main_country = country_code(task.get("main_country"))
    cross_country = country_code(task.get("cross_country"))
    if cross_country not in SITE_OPTIONS:
        _release_heartbeat(task)
        record_enqueue_rejection(
            config,
            database_path,
            task,
            "COUNTRY_INVALID",
            f"当前展开的账户列表里没有 {cross_country} 的站点选项",
            writer=writer,
        )
        _stage(on_progress, config, task, f"国家无效：当前展开的账户列表里没有 {cross_country}", done=1, total=1)
        return None
    sources = task.get("_addresses")
    if sources is None:
        sources = image_src_list(task.get("images"))
    log_business(logger, task, "site_images", "下载 " + str(len(sources)) + " 张 " + " ".join(sources))
    try:
        folder = picture_directory(picture_root, main_country, sku or asin, cross_country)
        if task.get("_addresses") is None:
            save_task_pictures(folder, task.get("images"))
        else:
            save_address_pictures(folder, sources)
    except PictureSourceError as exc:
        _release_heartbeat(task)
        if exc.code in _SOURCE_CODES:
            record_enqueue_rejection(config, database_path, task, exc.code, str(exc), writer=writer)
            _stage(on_progress, config, task, f"下载失败：{exc}", done=1, total=1)
        else:
            _fail(config, database_path, task, exc.code, str(exc), "site_images", writer, on_progress)
        return None
    _stage(on_progress, config, task, f"下载完成 {len(sources)} 张")
    return folder


def _countries(config, database_path, task, writer=None, on_progress=None):
    """图片已成功、只重跑 A+ 时才走到这里。国家仍须能识别。"""
    try:
        main_country = country_code(task.get("main_country"))
        cross_country = country_code(task.get("cross_country"))
    except ValueError as exc:
        _release_heartbeat(task)
        record_enqueue_rejection(config, database_path, task, "COUNTRY_INVALID", str(exc), writer=writer)
        _stage(on_progress, config, task, "国家无效：" + str(exc), done=1, total=1)
        return "", ""
    if cross_country not in SITE_OPTIONS:
        _release_heartbeat(task)
        record_enqueue_rejection(
            config,
            database_path,
            task,
            "COUNTRY_INVALID",
            f"当前展开的账户列表里没有 {cross_country} 的站点选项",
            writer=writer,
        )
        _stage(on_progress, config, task, f"国家无效：当前展开的账户列表里没有 {cross_country}", done=1, total=1)
        return "", ""
    return main_country, cross_country


def _upload(session, task, main_country, cross_country, folder, timeout, log_path, on_progress=None, config=None):
    keyword = str(task.get("sku") or "").strip() or str(task.get("asin") or "").strip()
    return_to_imaging_manage(session, main_country, timeout=timeout)
    slots = upload_images(
        session,
        keyword,
        cross_country,
        folder,
        timeout=timeout,
        log_path=log_path,
        log_context={
            "keyword": keyword,
            "task_id": task.get("task_id"),
            "sku": task.get("sku"),
            "asin": task.get("asin"),
            "country": main_country,
            "market": cross_country,
        },
        on_progress=lambda text: _stage(on_progress, config, task, _slot_stage_text(text), refresh=True),
        before_each=lambda: heartbeat.raise_if_lost(task),
    )
    failed = [item for item in slots if item.get("status") != "成功"]
    if not slots:
        raise NeedsManual("没有读到数字图片")
    if failed:
        done = [str(item.get("page_slot") or "") for item in slots if item.get("status") == "成功"]
        reason = "；".join(str(item.get("reason") or "") for item in failed if item.get("reason")) or "有图片未成功"
        if done:
            reason = reason + "；已成功 " + "、".join(done)
        raise NeedsManual(reason)


def _submit_aplus(session, task, main_country, timeout):
    switch_to_description_page(session, main_country, timeout=timeout)
    before = search_description(session, task.get("asin"), task.get("sku"), timeout=timeout)
    open_matched_description(session, task.get("sku"), timeout=timeout, previous=before)
    submit_for_approval(session, timeout=timeout, asin=task.get("asin"))


def _hold_lost(database_path, task):
    """这一行已被心跳收回。只改本机为待重试，不回写 CL，避免和随后的领取抢同一页。"""
    log_business(logger, task, "heartbeat", "CL 行已超过更新时限，已收回并停止执行")
    store = open_store(database_path)
    try:
        store.execute(
            """
            UPDATE task
            SET status = '待重试', error_code = ?, error_msg = ?, cl_pending = 0, updated_at = ?
            WHERE id = ? AND status = '处理中'
            """,
            (CL_LOST, "CL 行已超过更新时限，已收回并停止执行", _now_text(), task["id"]),
        )
        store.commit()
    finally:
        close_store(store)


def _now_text():
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fail(config, database_path, task, code, message, action, writer=None, on_progress=None):
    label = "站点图" if action == "site_images" else "A+" if action == "aplus" else action
    _stage(on_progress, config, task, f"{label} 失败：{message}", done=1, total=1, refresh=True)
    if code in _SOURCE_CODES:
        record_enqueue_rejection(config, database_path, task, code, message, writer=writer)
        return
    record_execution_failure(config, database_path, task, code, message, action, writer=writer)


def _stage(on_progress, config, task, text, done=0, total=0, refresh=False):
    """写到左侧进度。Excel 走传入的回调，数据库任务走展示页监听。"""
    line = describe(config, task, text)
    if on_progress is not None:
        on_progress(line, done, total)
        return
    report(line, done, total, refresh_records=refresh)


def _slot_stage_text(text):
    """把文件序号换成页面位置，例如 00 写成 MAIN。"""
    parts = str(text or "").split()
    if len(parts) == 2 and parts[0] == "上传" and parts[1].isdigit():
        return "上传 " + _stem_slot(parts[1])
    if len(parts) >= 2 and parts[0].isdigit():
        return _stem_slot(parts[0]) + " " + " ".join(parts[1:])
    return str(text or "")


def _stem_slot(stem):
    number = int(stem)
    if number == 0:
        return "MAIN"
    return f"PT{number:02d}"


def _failure_code(exc, action):
    text = str(exc)
    if isinstance(exc, BrowserConnectionError):
        return "BROWSER_CONNECT_FAILED", text
    if isinstance(exc, PageNavigationError):
        return "PAGE_NOT_READY", text
    if action == "site_images":
        if "不是 1 件" in text or "不一致" in text:
            return "IMG_SEARCH_NOT_UNIQUE", text
        if text.startswith("搜索结果未加载"):
            return "IMG_SEARCH_TIMEOUT", text
        if "位置" in text or "文件框" in text or "上传" in text or text.startswith("没有读到数字图片"):
            return "IMG_SLOT_FAILED", text
        if "等待" in text or "超时" in text or "找不到" in text:
            return "PAGE_NOT_READY", text
        return "UNKNOWN", text
    if "无法读取已应用" in text:
        return "PAGE_NOT_READY", text
    if "等待描述搜索结果超时" in text or "等待描述列表超时" in text:
        return "APLUS_SEARCH_TIMEOUT", text
    if "提交" in text or "已提交" in text or "修订" in text:
        return "APLUS_SUBMIT_FAILED", text
    if "等待" in text or "超时" in text:
        return "PAGE_NOT_READY", text
    return "UNKNOWN", text
