"""程序入口。先打开卖家后台，再选择 Excel，选中后读取并上传。"""

import sys
from pathlib import Path

from app.browser import launch_chrome
from app.config_loader import load_config
from app.file_pick import explain_read_failure
from app.import_ui import ImportWindow, _report_text
from app.logging_setup import setup_logging
from app.pipeline import run


def project_root():
    """开发时用源码目录。打包后用随 exe 带上的资源目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


ROOT = project_root()


def database_path(config):
    """队列文件路径。相对路径按项目目录解析。"""
    raw = str(config.get("database_file") or "data/queue.sqlite3").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    return path


def main():
    """先拉起独立 Chrome 打开卖家后台，再打开选择窗口。选中后读取并上传。"""
    config = load_config(ROOT / "config" / "settings.json")
    setup_logging(config.get("log_dir"), config.get("log_level"))
    country = str((config.get("browser") or {}).get("startup_country") or "").strip()
    if not country:
        print("配置里没有 browser.startup_country，无法先打开网页。", flush=True)
        return
    try:
        launch_chrome(country)
    except Exception as exc:
        print(str(exc), flush=True)
        return
    window = ImportWindow(database_path(config), ROOT / "picture_log.md")
    window.run()
    if window.selected_path is None:
        return
    try:
        report = run(window.selected_path, window.database_path, window.log_path)
    except Exception as exc:
        print(explain_read_failure(exc, window.selected_path), flush=True)
        return
    print(_report_text(report), flush=True)


if __name__ == "__main__":
    main()
