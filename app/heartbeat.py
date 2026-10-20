"""执行 CL 任务期间刷新 UPDATED_DATE。不改状态，不改错误信息。"""

import logging
import threading
import time

from app.logging_setup import log_business

logger = logging.getLogger(__name__)

CL_LOST = "CL_LOST"
_LOST_MESSAGE = "CL 行已不是 Process，已停止执行"


class ClaimLost(Exception):
    """这一行已经不是本机持有的 Process。"""


class Heartbeat:
    """一条后台线程。只在本机正在执行的 CL 行上刷新时间。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._guid = ""
        self._task = {}
        self._lost_guid = ""
        self._due = 0.0
        self._config = None
        self._thread = None
        self._has_updated = None

    def start(self, config):
        """轮询启动时挂上。重复调用不另开线程。"""
        with self._lock:
            self._config = config
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, name="cl-heartbeat", daemon=True)
            self._thread.start()

    def begin(self, config, task):
        """CL 任务进入处理中时开始刷新。Excel 任务不刷新。"""
        if not _is_cl_task(task):
            return False
        self.start(config)
        with self._lock:
            self._guid = _guid(task)
            self._task = _task_view(task)
            self._lost_guid = ""
            self._due = 0.0
        return True

    def end(self, task):
        """离开处理中之前停掉这一行。已经写完终态后再看到 0 行不算丢失。"""
        guid = _guid(task)
        with self._lock:
            if self._guid == guid:
                self._guid = ""
                self._task = {}

    def raise_if_lost(self, task):
        """这一行已被回收或被别人领走时，中止当前执行。"""
        guid = _guid(task)
        with self._lock:
            lost = bool(guid) and self._lost_guid == guid
        if lost:
            raise ClaimLost(_LOST_MESSAGE)

    def _loop(self):
        while True:
            time.sleep(1)
            self._tick()

    def _tick(self):
        with self._lock:
            guid = self._guid
            task = dict(self._task)
            config = self._config
            due = self._due
            if not guid or config is None or time.monotonic() < due:
                return
            self._due = time.monotonic() + _interval(config)
        try:
            count = self._touch(config, guid)
        except Exception:
            logger.exception(
                "心跳失败 TASK_ID=%s AMZ_CROSS_TASK_GUID=%s",
                task.get("task_id"),
                guid,
            )
            return
        if count is None:
            with self._lock:
                if self._guid == guid:
                    self._guid = ""
                    self._task = {}
            logger.info("CL 表没有 UPDATED_DATE，这一行不心跳 AMZ_CROSS_TASK_GUID=%s", guid)
            return
        if count == 1:
            log_business(logger, task, "heartbeat", "已刷新 UPDATED_DATE")
            return
        with self._lock:
            if self._guid != guid:
                return
            self._guid = ""
            self._task = {}
            self._lost_guid = guid
        log_business(logger, task, "heartbeat", _LOST_MESSAGE)

    def _touch(self, config, guid):
        """只刷新仍为 Process 的这一行。没有 UPDATED_DATE 时返回 None。"""
        from app.cl_task import _connect, _identifier, _require_db, _table_columns

        database = _require_db(config)
        table = _identifier(database.get("table"), "cl_db.table")
        connection = _connect(database, found_rows=True)
        try:
            if self._has_updated is None:
                self._has_updated = "UPDATED_DATE" in _table_columns(connection, table)
            if not self._has_updated:
                return None
            sql = (
                "UPDATE " + table
                + " SET UPDATED_DATE = NOW()"
                + " WHERE AMZ_CROSS_TASK_GUID = %s AND TASK_STATUS = %s"
            )
            with connection.cursor() as cursor:
                cursor.execute(sql, (guid, "Process"))
                count = cursor.rowcount
            if count == 1:
                connection.commit()
            else:
                connection.rollback()
            return 1 if count == 1 else 0
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


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


def _interval(config):
    """回收时间的三分之一，并且短于回收时间。最短 30 秒。"""
    database = config.get("cl_db") if isinstance(config, dict) else None
    raw = database.get("stale_processing_seconds") if isinstance(database, dict) else None
    try:
        stale = float(raw)
    except (TypeError, ValueError):
        stale = 1800
    if stale <= 0:
        stale = 1800
    interval = stale / 3
    if interval < 30:
        interval = 30
    if interval >= stale:
        interval = stale / 2
    return interval


heartbeat = Heartbeat()
