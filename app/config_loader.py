"""读取配置文件。"""

import json
from pathlib import Path


def load_config(path):
    """读取 JSON 配置并返回字典。"""
    file = Path(path)
    if not file.is_file():
        raise FileNotFoundError("找不到配置文件：" + str(file))
    with file.open(encoding="utf-8") as handle:
        return json.load(handle)
