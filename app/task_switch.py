"""按本机 task 打开主站网址并切到跨站。不上传，不提交，不回写 CL。"""

import logging

from app.browser import close_browser, connect_country, country_code, cross_site_code, switch_site
from app.queue_store import close_store, mark_task_retry, open_store, take_pending_task

logger = logging.getLogger(__name__)


def switch_one_pending(database_path, timeout=30):
    """有待处理且当前没有处理中时，用 MAIN_COUNTRY 开网址，用 CROSS_COUNTRY 切站点。

    切完保持处理中，避免下一轮重复切。失败改为待重试。
    不下载图片，不上传，不提交 A+，不改 CL。
    """
    store = open_store(database_path)
    task = None
    try:
        task = take_pending_task(store)
        if task is None:
            return False
        _switch(task, timeout)
    except Exception as exc:
        if task is not None:
            mark_task_retry(store, task["id"], str(exc))
        logger.exception(
            "切站点失败 TASK_ID=%s AMZ_CROSS_TASK_GUID=%s",
            None if task is None else task.get("task_id"),
            None if task is None else task.get("amz_cross_task_guid"),
        )
        return False
    finally:
        close_store(store)
    logger.info(
        "已用 %s 打开网址并切到 %s TASK_ID=%s AMZ_CROSS_TASK_GUID=%s",
        task.get("main_country"),
        task.get("cross_country"),
        task.get("task_id"),
        task.get("amz_cross_task_guid"),
    )
    return True


def _switch(task, timeout):
    main_country = country_code(task.get("main_country"))
    cross_country = cross_site_code(task.get("cross_country"))
    session = connect_country(main_country, timeout=timeout)
    try:
        switch_site(session, cross_country, timeout=timeout)
    finally:
        close_browser(session)
