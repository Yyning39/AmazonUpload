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
