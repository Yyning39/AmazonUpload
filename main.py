"""程序入口。打开账号与上传记录窗口。五国 Chrome 等窗口里点击后再拉起。"""

import sys
from pathlib import Path

import logging

from app.config_loader import app_dir, bundle_dir, load_config
from app.flet_hub import HubWindow
from app.logging_setup import setup_logging
from app.progress_feed import source_note
from app.queue_store import close_store, open_store, release_processing_tasks


def database_path(config):
    """队列文件路径。相对路径写到 exe 旁边，不写进临时解压目录。"""
    raw = str(config.get("database_file") or "data/queue.sqlite3").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = app_dir() / path
    return path


def _hide_frozen_console():
    """打包后的窗口程序不留命令行。开发时从终端启动则保留。"""
    if not getattr(sys, "frozen", False) or sys.platform != "win32":
        return
    import ctypes

    window = ctypes.windll.kernel32.GetConsoleWindow()
    if window:
        ctypes.windll.user32.ShowWindow(window, 0)


def main():
    """打开账号管理窗口。不自动拉起 Chrome。"""
    _hide_frozen_console()
    settings = bundle_dir() / "config" / "settings.json"
    try:
        config = load_config(settings)
    except FileNotFoundError:
        window = HubWindow(
            app_dir() / "data" / "queue.sqlite3",
            app_dir() / "picture_log.md",
            app_dir() / "aplus_log.md",
            "未找到 config/settings.json，无法连接数据库。",
            {},
        )
        window.run()
        return
    log_dir = Path(str(config.get("log_dir") or "logs"))
    if not log_dir.is_absolute():
        log_dir = app_dir() / log_dir
    setup_logging(log_dir, config.get("log_level"))
    path = database_path(config)
    store = open_store(path)
    try:
        released = release_processing_tasks(store)
    finally:
        close_store(store)
    if released:
        logging.getLogger(__name__).info("启动时把 %s 条处理中改为待重试", released)
    window = HubWindow(
        database_path(config),
        app_dir() / "picture_log.md",
        app_dir() / "aplus_log.md",
        source_note(config),
        config,
    )
    window.run()


if __name__ == "__main__":
    main()
