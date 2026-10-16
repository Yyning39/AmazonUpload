"""程序入口。打开账号与上传记录窗口，并启动欧洲五国 Chrome。"""

import sys
from pathlib import Path

from app.config_loader import load_config
from app.hub_ui import HubWindow
from app.logging_setup import setup_logging


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
    """打开账号管理窗口。窗口里会启动英国、德国、法国、意大利、西班牙。"""
    config = load_config(ROOT / "config" / "settings.json")
    setup_logging(config.get("log_dir"), config.get("log_level"))
    window = HubWindow(database_path(config), ROOT / "picture_log.md", ROOT / "aplus_log.md")
    window.run()


if __name__ == "__main__":
    main()
