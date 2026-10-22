"""执行中定期刷新 UPDATED_DATE。另把超时仍为 Process 的行收回成 New。"""

import logging
import threading
import time

from app.logging_setup import log_business

logger = logging.getLogger(__name__)

CL_LOST = "CL_LOST"
_LOST_MESSAGE = "CL 行已超过更新时限，已收回并停止执行"


class ClaimLost(Exception):
    """这一行已经不是本机持有的 Process。"""


class Heartbeat:
    """一条后台线程。每个正在执行的国家各刷新自己的行；其余超时的 Process 收回成 New。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._running = {}
        self._refresh_due = {}
        self._lost = set()
        self._reclaim_due = 0.0
        self._config = None
        self._thread = None

    def start(self, config):
        """轮询启动时挂上。重复调用不另开线程。"""
        with self._lock:
            self._config = config
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, name="cl-heartbeat", daemon=True)
            self._thread.start()

    def begin(self, config, task):
        """开始执行或开始重试时先刷新这一行。Excel 任务不刷新。五国可以同时各记一行。"""
        if not _is_cl_task(task):
            return False
        self.start(config)
        guid = _guid(task)
        view = _task_view(task)
        with self._lock:
            self._running[guid] = view
            self._lost.discard(guid)
            self._config = config
            self._refresh_due[guid] = time.monotonic() + _refresh_interval(config)
        self._touch(config, view, guid)
        return True

    def end(self, task):
        """离开处理中之前停掉这一行。已经写完终态后再看到 0 行不算丢失。"""
        guid = _guid(task)
        with self._lock:
            self._running.pop(guid, None)
            self._refresh_due.pop(guid, None)

    def raise_if_lost(self, task):
        """这一行已被回收或被别人领走时，中止当前执行。"""
        guid = _guid(task)
        with self._lock:
            lost = bool(guid) and guid in self._lost
        if lost:
            raise ClaimLost(_LOST_MESSAGE)

    def _loop(self):
        while True:
            time.sleep(1)
            self._tick()

    def _tick(self):
        now = time.monotonic()
        with self._lock:
            config = self._config
            if config is None:
                return
            due = [
                (guid, dict(task))
                for guid, task in self._running.items()
                if now >= self._refresh_due.get(guid, 0)
            ]
            interval = _refresh_interval(config)
            for guid, _task in due:
                self._refresh_due[guid] = now + interval
            do_reclaim = now >= self._reclaim_due
            if do_reclaim:
                self._reclaim_due = now + _reclaim_interval(config)
            active = list(self._running)
        for guid, task in due:
            self._touch(config, task, guid)
        if not do_reclaim:
            return
        try:
            from app.cl_task import reclaim_stale

            reclaimed = reclaim_stale(config, exclude_guids=active)
        except Exception:
            logger.exception("心跳回收僵尸 Process 失败")
            return
        if reclaimed:
            logger.info("心跳收回僵尸 Process %s 条", len(reclaimed))

    def _touch(self, config, task, guid):
        """刷新正在执行的这一行。行已不是 Process 时停止它。"""
        if not guid:
            return
        try:
            from app.cl_task import touch_updated

            count = touch_updated(config, guid)
        except Exception:
            logger.exception(
                "心跳刷新失败 TASK_ID=%s AMZ_CROSS_TASK_GUID=%s",
                task.get("task_id"),
                guid,
            )
            return
        if count is None:
            logger.info("CL 表没有 UPDATED_DATE，这一行不心跳 AMZ_CROSS_TASK_GUID=%s", guid)
            return
        if count == 1:
            log_business(logger, task, "heartbeat", "已刷新 UPDATED_DATE")
            return
        with self._lock:
            if guid not in self._running:
                return
            self._running.pop(guid, None)
            self._refresh_due.pop(guid, None)
            self._lost.add(guid)
        log_business(logger, task, "heartbeat", _LOST_MESSAGE)


def _is_cl_task(task):
    if str(task.get("origin") or "cl").strip().lower() != "cl":
        return False
    guid = _guid(task)
    return bool(guid) and not guid.startswith("excel:")


def _guid(task):
    return str((task or {}).get("amz_cross_task_guid") or "").strip()


def _task_view(task):
    return {
        "task_id": task.get("task_id"),
        "amz_cross_task_guid": _guid(task),
        "sku": task.get("sku"),
        "asin": task.get("asin"),
        "cross_country": task.get("cross_country"),
        "attempt": task.get("attempt"),
    }


def _refresh_interval(config):
    """执行中多久刷新一次。取回收时间的三分之一，最短 30 秒，并且短于回收时间。"""
    stale = _stale_seconds(config)
    interval = stale / 3
    if interval < 30:
        interval = 30
    if interval >= stale:
        interval = stale / 2
    return interval


def _reclaim_interval(config):
    """多久查找一次僵尸 Process。用轮询间隔，默认 30 秒。"""
    database = config.get("cl_db") if isinstance(config, dict) else None
    raw = database.get("poll_interval_seconds") if isinstance(database, dict) else None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        seconds = 30
    if seconds <= 0:
        return 30
    return seconds


def _stale_seconds(config):
    database = config.get("cl_db") if isinstance(config, dict) else None
    raw = database.get("stale_processing_seconds") if isinstance(database, dict) else None
    try:
        stale = float(raw)
    except (TypeError, ValueError):
        stale = 1800
    if stale <= 0:
        return 1800
    return stale


heartbeat = Heartbeat()
