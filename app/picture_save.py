"""按 src 里的地址把图片存到本地目录。不读 Excel，不打开浏览器，不上传。"""

import json
import re
import shutil
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app.browser import country_code

# 与 plan.md 第 6 节、图片上传允许的扩展名相同。
IMAGE_EXTENSIONS = {".gif", ".pjp", ".jfif", ".jpe", ".pjpeg", ".jpeg", ".jpg", ".png"}
# 设计文档：不是完整网址时，拼到这个 CDN 后面再下载。
CDN_PREFIX = "https://img-cl.aosomcdn.com/"
_CONTENT_TYPES = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/pjpeg": ".pjpeg",
}
_SPLIT = re.compile(r"[\r\n;；|]+")


def picture_directory(picture_root, country, sku, site):
    """图片目录：picture_root/<国家代码>/<sku>/<站点代码>。西班牙写成 ES。"""
    sku_text = str(sku or "").strip()
    if not sku_text:
        raise ValueError("sku 为空，无法保存图片")
    return Path(picture_root) / country_code(country) / sku_text / country_code(site)


class PictureSourceError(ValueError):
    """本机 task 的 images 不能下载。code 用入队或类型错误码。"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def task_file_stem(index):
    """第 0 张文件名是 0，第 1 张是 01，第 2 张是 02。"""
    if index == 0:
        return "0"
    return f"{index:02d}"


def image_src_list(images_text):
    """从 task.images 取出每张图的 src。

    设计文档的示例是 JSON 数组，每一项本身就是完整网址，这一项就是 src。
    下标 0 对应 MAIN，1 对应 PT01。
    """
    if isinstance(images_text, list):
        data = images_text
    else:
        text = str(images_text or "").strip()
        if not text:
            raise PictureSourceError("IMG_EMPTY", "IMAGES 为空")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PictureSourceError("IMG_URL_INVALID", "IMAGES 不是 JSON 数组") from exc
    if not isinstance(data, list):
        raise PictureSourceError("IMG_URL_INVALID", "IMAGES 不是 JSON 数组")
    if not data:
        raise PictureSourceError("IMG_EMPTY", "IMAGES 为空")
    addresses = []
    for index, value in enumerate(data):
        item = _image_src_item(value)
        if not item:
            raise PictureSourceError("IMG_ITEM_EMPTY", f"IMAGES[{index}] 为空")
        if not item.lower().startswith(("http://", "https://")):
            raise PictureSourceError("IMG_URL_INVALID", f"IMAGES[{index}] 不是 http(s) 地址")
        addresses.append(item)
    return addresses


def task_image_addresses(images_text):
    """解析 task.images，返回按顺序的 src。"""
    return image_src_list(images_text)


def parse_image_addresses(text):
    """把 Excel 的 images 单元格拆成按顺序的地址。

    支持 JSON 数组、换行、分号、竖线和逗号。数组里的对象取 src。
    空项保留，方便入队校验标出下标。
    """
    raw = str(text or "").strip()
    if not raw:
        return []
    if raw.startswith("[") and raw.endswith("]"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            return [_image_src_item(value) for value in data]
    pieces = re.split(r"[\r\n;；|]", raw)
    if len(pieces) == 1 and "," in pieces[0]:
        pieces = [item.strip() for item in pieces[0].split(",")]
    else:
        pieces = [item.strip().strip("\"'") for item in pieces]
    return pieces


def save_address_pictures(folder, addresses):
    """按地址顺序存成 0、01、02…。完整网址下载，本机文件复制。"""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    temporary = []
    try:
        for index, address in enumerate(addresses):
            stem = task_file_stem(index)
            suffix, part = _store_address(address, folder, stem, index)
            if part.stat().st_size <= 0:
                raise PictureSourceError("IMG_DOWNLOAD_FAILED", f"第 {index} 张图片是空文件")
            temporary.append((part, folder / f"{stem}{suffix}"))
        keep = {final.name for _, final in temporary}
        for old in _numbered_files(folder):
            if old.name not in keep:
                old.unlink()
        saved = []
        for part, final in temporary:
            part.replace(final)
            saved.append(final)
        return saved
    finally:
        for part, _final in temporary:
            if part.exists():
                part.unlink()


def _store_address(address, folder, stem, index):
    text = str(address or "").strip()
    if _is_local_path(text):
        source = Path(text)
        if not source.is_file():
            raise PictureSourceError("IMG_DOWNLOAD_FAILED", f"找不到图片：{text}")
        suffix = _known_suffix(source.suffix)
        if not suffix:
            raise PictureSourceError("IMG_TYPE_UNSUPPORTED", f"IMAGES[{index}] 扩展名不支持")
        part = folder / f".task-{stem}{suffix}.part"
        shutil.copyfile(source, part)
        return suffix, part
    if not text.lower().startswith(("http://", "https://")):
        raise PictureSourceError("IMG_URL_INVALID", f"IMAGES[{index}] 不是 http(s) 地址")
    return _download_task_image(text, folder, stem, index)


def _image_src_item(value):
    """数组里的字符串就是 src。对象则取 src 字段。"""
    if isinstance(value, dict):
        return _item_text(value.get("src"))
    return _item_text(value)


def save_task_pictures(folder, images_text):
    """按 images 顺序下载，存成 0、01、02…。只接受完整网址，不拼 CDN。

    全部成功后才替换该目录里的旧数字图片。
    """
    addresses = task_image_addresses(images_text)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    temporary = []
    try:
        for index, address in enumerate(addresses):
            stem = task_file_stem(index)
            suffix, part = _download_task_image(address, folder, stem, index)
            if part.stat().st_size <= 0:
                raise PictureSourceError("IMG_DOWNLOAD_FAILED", f"第 {index} 张图片是空文件")
            temporary.append((part, folder / f"{stem}{suffix}"))
        keep = {final.name for _, final in temporary}
        for old in _numbered_files(folder):
            if old.name not in keep:
                old.unlink()
        saved = []
        for part, final in temporary:
            part.replace(final)
            saved.append(final)
        return saved
    finally:
        for part, _final in temporary:
            if part.exists():
                part.unlink()


def save_numbered_pictures(folder, src):
    """把 src 中的图片按顺序存成 01、02、03…，写到 folder。

    `[1,2,3]` 按顺序当成 1、2、3。完整网址直接下载，本机文件直接复制，其余拼到 CDN 后面再下载。
    全部成功后才替换该目录里的旧数字图片。
    """
    addresses = split_src(src)
    if not addresses:
        raise ValueError("src 没有图片")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    temporary = []
    try:
        for index, address in enumerate(addresses, 1):
            suffix, part = _fetch(address, folder, index)
            if part.stat().st_size <= 0:
                raise ValueError(f"第 {index} 张图片是空文件：{address}")
            temporary.append((part, folder / f"{index:02d}{suffix}"))
        keep = {final.name for _, final in temporary}
        for old in _numbered_files(folder):
            if old.name not in keep:
                old.unlink()
        saved = []
        for part, final in temporary:
            part.replace(final)
            saved.append(final)
        return saved
    finally:
        for part, _final in temporary:
            if part.exists():
                part.unlink()


def split_src(src):
    """把单元格拆成按顺序的图片项。`[1,2,3]` 得到 1、2、3。"""
    text = str(src or "").strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            return [item for item in (_item_text(value) for value in data) if item]
        inner = text[1:-1].strip()
        return [item.strip().strip("\"'") for item in inner.split(",") if item.strip()]
    parts = [item.strip() for item in _SPLIT.split(text) if item.strip()]
    if len(parts) == 1 and "," in parts[0]:
        parts = [item.strip() for item in parts[0].split(",") if item.strip()]
    return parts


def _item_text(value):
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value or "").strip()


def _download_task_image(address, folder, stem, index):
    """下载这一张完整网址。扩展名不在允许列表里时用 IMG_TYPE_UNSUPPORTED。"""
    suffix = _known_suffix(Path(urlparse(address).path).suffix)
    request = Request(address, headers={"User-Agent": "AosomImageAplus"})
    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read()
            if not suffix:
                content_type = str(response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                suffix = _CONTENT_TYPES.get(content_type, "")
    except (OSError, URLError, TimeoutError) as exc:
        raise PictureSourceError("IMG_DOWNLOAD_FAILED", f"图片下载失败：{address}：{exc}") from exc
    if not suffix:
        raise PictureSourceError("IMG_TYPE_UNSUPPORTED", f"IMAGES[{index}] 扩展名不支持")
    part = folder / f".task-{stem}{suffix}.part"
    part.write_bytes(payload)
    return suffix, part


def _fetch(address, folder, index):
    """把这一项存成临时文件，并返回扩展名和临时路径。"""
    text = str(address or "").strip()
    if _is_local_path(text):
        source = Path(text)
        if not source.is_file():
            raise ValueError("找不到图片：" + text)
        suffix = _known_suffix(source.suffix)
        if not suffix:
            raise ValueError(f"第 {index} 张图片扩展名不支持：{text}")
        part = folder / f".{index:02d}{suffix}.part"
        shutil.copyfile(source, part)
        return suffix, part
    url = text if text.lower().startswith(("http://", "https://")) else CDN_PREFIX + text.lstrip("/")
    suffix = _known_suffix(Path(urlparse(url).path).suffix)
    request = Request(url, headers={"User-Agent": "AosomImageAplus"})
    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read()
            if not suffix:
                content_type = str(response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                suffix = _CONTENT_TYPES.get(content_type, "")
    except (OSError, URLError, TimeoutError) as exc:
        raise ValueError(f"图片下载失败：{url}：{exc}") from exc
    if not suffix:
        raise ValueError(f"第 {index} 张图片没有扩展名：{text}")
    part = folder / f".{index:02d}{suffix}.part"
    part.write_bytes(payload)
    return suffix, part


def _is_local_path(text):
    if text.lower().startswith(("http://", "https://")):
        return False
    if re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith(("\\\\", "/", "\\")):
        return True
    return Path(text).is_file()


def _known_suffix(suffix):
    text = str(suffix or "").lower()
    if text in IMAGE_EXTENSIONS:
        return text
    return ""


def _numbered_files(folder):
    files = []
    for path in folder.iterdir():
        if path.is_file() and path.stem.isdigit() and path.suffix.lower() in IMAGE_EXTENSIONS:
            files.append(path)
    return files
