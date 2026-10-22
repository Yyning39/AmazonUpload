"""入队校验。不通过时调用方写成失败，不下载，不开浏览器。"""

from pathlib import Path

from app.browser import country_code
from app.picture_save import PictureSourceError, _is_local_path, image_src_list


def check_enqueue(task):
    """数据库任务。images 必须是 JSON 数组里的完整网址。"""
    problem = _check_identity(task)
    if problem is not None:
        return problem
    try:
        image_src_list(task.get("images"))
    except PictureSourceError as exc:
        return exc.code, str(exc)
    if not str(task.get("asin") or "").strip():
        return "ASIN_EMPTY", "ASIN 为空"
    return None


def check_excel(task, addresses):
    """Excel 任务。地址可以是完整网址，也可以是已经存在的本机文件。"""
    problem = _check_identity(task)
    if problem is not None:
        return problem
    problem = _check_addresses(addresses)
    if problem is not None:
        return problem
    if not str(task.get("asin") or "").strip():
        return "ASIN_EMPTY", "ASIN 为空"
    return None


def _check_identity(task):
    if not str(task.get("task_id") or "").strip():
        return "TASK_ID_EMPTY", "TASK_ID 为空"
    for field, label in (("main_country", "MAIN_COUNTRY"), ("cross_country", "CROSS_COUNTRY")):
        try:
            country_code(task.get(field))
        except ValueError:
            return "COUNTRY_INVALID", f"{label} 无法识别为七国代码"
    sku = str(task.get("sku") or "").strip()
    asin = str(task.get("asin") or "").strip()
    if not sku and not asin:
        return "SKU_ASIN_EMPTY", "SKU 和 ASIN 都为空"
    return None


def _check_addresses(addresses):
    if not addresses:
        return "IMG_EMPTY", "IMAGES 为空"
    for index, value in enumerate(addresses):
        item = str(value or "").strip()
        if not item:
            return "IMG_ITEM_EMPTY", f"IMAGES[{index}] 为空"
        if item.lower().startswith(("http://", "https://")):
            continue
        if _is_local_path(item) and Path(item).is_file():
            continue
        return "IMG_URL_INVALID", f"IMAGES[{index}] 不是 http(s) 地址或本机文件"
