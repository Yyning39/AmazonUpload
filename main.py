"""程序入口。打开选择 Excel 的窗口，选中后读取、打开网页并上传。"""

from pathlib import Path

from app.config_loader import load_config
from app.file_pick import explain_read_failure
from app.import_ui import ImportWindow, _report_text
from app.logging_setup import setup_logging
from app.pipeline import run

ROOT = Path(__file__).resolve().parent


def database_path(config):
    """队列文件路径。相对路径按项目目录解析。"""
    raw = str(config.get("database_file") or "data/queue.sqlite3").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    return path


def main():
    """打开选择窗口。表格读入后窗口关闭，再打开网页并上传。"""
    config = load_config(ROOT / "config" / "settings.json")
    setup_logging(config.get("log_dir"), config.get("log_level"))
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
