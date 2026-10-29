"""初始化日志。按天写入 logs/YYYY-MM-DD.log。"""

import logging
import threading
from datetime import datetime
from pathlib import Path


class DailyFileHandler(logging.Handler):
    """每次写出时按当天日期选择文件。跨过零点后写到新的一天。"""

    def __init__(self, directory):
        super().__init__()
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._date = ""
        self._stream = None

    def emit(self, record):
        try:
            with self._lock:
                day = datetime.now().strftime("%Y-%m-%d")
                if day != self._date or self._stream is None:
                    self._open(day)
                self._stream.write(self.format(record) + "\n")
                self._stream.flush()
        except Exception:
            self.handleError(record)

    def _open(self, day):
        if self._stream is not None:
            self._stream.close()
        self._date = day
        self._stream = (self.directory / (day + ".log")).open("a", encoding="utf-8")

    def close(self):
        try:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
        finally:
            super().close()


def setup_logging(log_dir, level):
    """确定日志目录和级别，并挂上按天切换的文件。"""
    directory = Path(log_dir or "logs")
    directory.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level or "INFO").upper(), logging.INFO))
    if any(isinstance(handler, DailyFileHandler) for handler in root.handlers):
        return
    handler = DailyFileHandler(directory)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)


def log_business(logger, task, action, message):
    """业务日志带上任务号、货号、站点、步骤和次数。不写密码。"""
    logger.info(
        "TASK_ID=%s AMZ_CROSS_TASK_GUID=%s sku=%s asin=%s cross_country=%s action=%s attempt=%s %s",
        task.get("task_id") or "",
        task.get("amz_cross_task_guid") or "",
        task.get("sku") or "",
        task.get("asin") or "",
        task.get("cross_country") or "",
        action or "",
        task.get("attempt") if task.get("attempt") is not None else "",
        message,
    )
