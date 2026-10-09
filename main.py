"""程序入口。当前只打开导入窗口。"""

from pathlib import Path

from app.config_loader import load_config
from app.import_ui import ImportWindow
from app.logging_setup import setup_logging

ROOT = Path(__file__).resolve().parent


def database_path(config):
    """队列文件路径。相对路径按项目目录解析。"""
    raw = str(config.get("database_file") or "data/queue.sqlite3").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    return path


def main():
    """加载配置并打开选择/拖入窗口。不进入上传或回写。"""
    config = load_config(ROOT / "config" / "settings.json")
    setup_logging(config.get("log_dir"), config.get("log_level"))
    ImportWindow(database_path(config)).run()


if __name__ == "__main__":
    main()
