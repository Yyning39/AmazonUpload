"""读取配置文件。"""

import copy
import json
import sys
from pathlib import Path


def _deep_merge(base, overlay):
    """把 overlay 递归合并进 base（同名字典则继续合并，其余覆盖）。"""
    if not isinstance(base, dict) or not isinstance(overlay, dict):
        return overlay
    merged = copy.deepcopy(base)
    for key, value in overlay.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _local_config_candidates(settings_path):
    """settings.local.json 候选路径：开发目录优先，打包后还可放在 exe 旁。"""
    settings_path = Path(settings_path)
    candidates = [settings_path.with_name("settings.local.json")]
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "config" / "settings.local.json")
        candidates.append(exe_dir / "settings.local.json")
    return candidates


def load_config(path):
    """读取 JSON 配置并返回字典。

    若存在 settings.local.json，则深度合并其上（账号密码等私密项放这里，不进仓库）。
    """
    file = Path(path)
    if not file.is_file():
        raise FileNotFoundError("找不到配置文件：" + str(file))
    with file.open(encoding="utf-8") as handle:
        config = json.load(handle)

    for local in _local_config_candidates(file):
        if local.is_file():
            with local.open(encoding="utf-8") as handle:
                config = _deep_merge(config, json.load(handle))
            break
    return config
