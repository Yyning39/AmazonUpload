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


def bundle_dir():
    """只读资源目录。开发时是项目根；打包后是随 exe 解压出来的资源。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", app_dir()))
    return Path(__file__).resolve().parent.parent


def app_dir():
    """可写目录。开发时是项目根；打包后是 exe 所在目录，换位置也能写日志和队列。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _local_config_candidates(settings_path):
    """settings.local.json：先看配置旁边，打包后再看 exe 旁边。"""
    settings_path = Path(settings_path)
    candidates = [settings_path.with_name("settings.local.json")]
    if getattr(sys, "frozen", False):
        exe_dir = app_dir()
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
    return _apply_environment(config)


def _apply_environment(config):
    """按 env 选用 environments 里的连接项，覆盖到顶层。

    未配置 environments 时保持原样，兼容只写一份 cl_db 的旧文件。
    """
    environments = config.get("environments")
    if not isinstance(environments, dict) or not environments:
        return config
    name = str(config.get("env") or "").strip()
    if not name:
        return config
    selected = environments.get(name)
    if not isinstance(selected, dict):
        known = "、".join(str(key) for key in environments)
        raise ValueError("未知环境「" + name + "」，可选：" + known)
    return _deep_merge(config, selected)
