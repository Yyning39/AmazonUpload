"""本机 Chrome 的国家端口、启动首页，以及进入图片管理页。"""

import json
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from app.cdp import BrowserConnectionError, PageSession, connect_port, list_targets

# 与原项目独立 Chrome 相同：按 US、CA、UK、DE、FR、IT、ES 从 9222 递增。
COUNTRY_PORTS = {
    "US": 9222,
    "CA": 9223,
    "UK": 9224,
    "DE": 9225,
    "FR": 9226,
    "IT": 9227,
    "ES": 9228,
}
# 启动 Chrome 时打开的第一个页面：该国 Seller Central 首页。
SELLER_CENTRAL_DOMAINS = {
    "US": "sellercentral.amazon.com",
    "CA": "sellercentral.amazon.ca",
    "UK": "sellercentral.amazon.co.uk",
    "DE": "sellercentral.amazon.de",
    "FR": "sellercentral.amazon.fr",
    "IT": "sellercentral.amazon.it",
    "ES": "sellercentral.amazon.es",
}
COUNTRY_NAMES = {
    "美国": "US",
    "加拿大": "CA",
    "英国": "UK",
    "德国": "DE",
    "法国": "FR",
    "意大利": "IT",
    "西班牙": "ES",
}
# 原项目复用日常 Chrome 时的端口。本项目不走这条路径。
REUSE_PORT = 9230
# 各国 Seller Central 图片管理页路径相同，只换域名。
IMAGING_MANAGE_PATH = "/imaging/manage"
# 该标记出现在图片管理页内容里，不在 Seller Central 通用顶栏上。
# 这是在西班牙已登录页面上看到的，原项目没有这一页。
IMAGING_MANAGE_MARKER = '[data-testid="image-manager-tab"]'


def country_code(country):
    """把 US 或「美国」转成国家代码。"""
    text = str(country or "").strip()
    code = text.upper()
    if code not in COUNTRY_PORTS:
        code = COUNTRY_NAMES.get(text, "")
    if code not in COUNTRY_PORTS:
        raise ValueError("未知国家站点：" + text)
    return code


def port_for(country):
    """返回该国家专用 Chrome 的调试端口。"""
    return COUNTRY_PORTS[country_code(country)]


def home_url(country):
    """返回启动时打开的 Seller Central 首页。"""
    return "https://" + SELLER_CENTRAL_DOMAINS[country_code(country)]


def imaging_manage_url(country):
    """返回该国图片管理页的完整网址。

    域名来自 SELLER_CENTRAL_DOMAINS，路径固定为 /imaging/manage。
    """
    return home_url(country) + IMAGING_MANAGE_PATH


def profile_dir(country):
    """该国家专用的 Chrome 用户目录，与日常 Chrome 分开。"""
    root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AosomImageAplus" / "chrome-profiles"
    return root / country_code(country)


def chrome_executable():
    """查找本机 chrome.exe。"""
    candidates = []
    for root in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
        if root:
            candidates.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for path in candidates:
        if path.is_file():
            return str(path)
    raise FileNotFoundError("未找到 chrome.exe")


def launch_chrome(country):
    """单独拉起该国家的 Chrome。

    使用独立用户目录和该国调试端口，避免参数被已经打开的日常 Chrome 吃掉。
    端口已在监听时不再启动第二个进程。不点击、不填写页面。
    """
    code = country_code(country)
    port = port_for(code)
    url = home_url(code)
    profile = profile_dir(code)
    if _debug_port_open(port):
        return {"started": False, "port": port, "url": url, "profile": str(profile)}
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [
            chrome_executable(),
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={port}",
            f"--remote-allow-origins=http://127.0.0.1:{port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            url,
        ],
        close_fds=True,
    )
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if _debug_port_open(port):
            return {"started": True, "port": port, "url": url, "profile": str(profile)}
        time.sleep(0.25)
    raise BrowserConnectionError(f"独立 Chrome 已启动，但端口 {port} 没有开始监听")


def _debug_port_open(port):
    try:
        list_targets(port)
        return True
    except BrowserConnectionError:
        return False


class PageNavigationError(Exception):
    """已经连上 Chrome，但没有进入目标页。"""


def open_imaging_manage(country, timeout=30) -> PageSession:
    """连接该国 Chrome，并用 Page.navigate 打开图片管理页。

    成功时页面同时满足：网址是该国 /imaging/manage，
    document.readyState 为 interactive 或 complete，
    且图片管理页标记已出现。
    不点击菜单，不填写，不选择文件。

    连不上时抛出 BrowserConnectionError。
    停留在别的网址时抛出 PageNavigationError，说明当前网址。
    网址对了但页面未就绪时抛出 PageNavigationError，说明超时。
    """
    code = country_code(country)
    url = imaging_manage_url(code)
    session = _connect_country_page(code, timeout=min(float(timeout), 20))
    try:
        _navigate_and_wait(session, url, timeout)
    except Exception:
        session.close()
        raise
    return session


def _connect_country_page(country, timeout):
    """连到该国端口上的 Seller Central 标签。没有时再用其他普通网页。"""
    domain = SELLER_CENTRAL_DOMAINS[country_code(country)]
    port = port_for(country)
    deadline = time.monotonic() + float(timeout)
    last_error = None
    while True:
        session = None
        try:
            target = _choose_country_target(list_targets(port), domain)
            session = PageSession(target)
            session.confirm_control()
            return session
        except Exception as exc:
            last_error = exc
            if session is not None:
                session.close()
            if time.monotonic() >= deadline:
                raise BrowserConnectionError(
                    f"连接 {country_code(country)} 站 Chrome 失败（127.0.0.1:{port}）：{last_error}"
                ) from last_error
            time.sleep(0.5)


def _choose_country_target(targets, domain):
    pages = []
    for target in targets or []:
        if target.get("type") != "page":
            continue
        websocket_url = str(target.get("webSocketDebuggerUrl") or "")
        host = (urlparse(websocket_url).hostname or "").lower()
        if urlparse(websocket_url).scheme not in {"ws", "wss"} or host not in {"127.0.0.1", "localhost"}:
            continue
        pages.append(target)
    if not pages:
        raise BrowserConnectionError("Chrome 没有可控制的页面")
    for target in pages:
        if domain in str(target.get("url") or ""):
            return target
    for target in pages:
        page_url = str(target.get("url") or "")
        if page_url.startswith("https://"):
            return target
    return pages[0]


def _navigate_and_wait(session, url, timeout):
    """发 Page.navigate，然后轮询可观测条件，直到就绪或超时。

    Page.navigate 的调用方式参考原项目 ChromePage.navigate。
    轮询 readyState 的方式参考原项目 open_upload_page。
    原项目在导航后有一段固定等待；这里不以那段等待作为成功条件。
    """
    result = session.call("Page.navigate", {"url": url})
    error_text = str((result or {}).get("errorText") or "").strip()
    if error_text:
        raise PageNavigationError("页面导航被拒绝：" + error_text)
    expected = urlparse(url)
    deadline = time.monotonic() + float(timeout)
    last = {}
    while True:
        last = session.evaluate(
            "(() => ({href: location.href, readyState: document.readyState,"
            f" marker: !!document.querySelector({json.dumps(IMAGING_MANAGE_MARKER)})}}))()"
        ) or {}
        if _imaging_page_ready(last, expected):
            return
        if time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    current = str(last.get("href") or "")
    if not _same_page(current, expected):
        raise PageNavigationError("页面不对，当前网址是：" + (current or "空"))
    ready = last.get("readyState") or "未知"
    marker = "已出现" if last.get("marker") else "未出现"
    raise PageNavigationError(f"导航超时：readyState={ready}，图片管理标记{marker}")


def _imaging_page_ready(state, expected):
    ready = state.get("readyState") in {"interactive", "complete"}
    return ready and bool(state.get("marker")) and _same_page(state.get("href"), expected)


def _same_page(current, expected):
    parsed = urlparse(str(current or ""))
    path = parsed.path.rstrip("/") or "/"
    return (parsed.hostname or "").lower() == expected.hostname.lower() and path == expected.path.rstrip("/")


def connect_browser(host, port, timeout=20) -> PageSession:
    """连接已在本机端口上开启调试的 Chrome。

    发现标签、建立 WebSocket，并用四条 CDP 命令确认可控。
    不启动 Chrome，不打开网址，也不填写页面。
    """
    return connect_port(port, host, timeout)


def submit_item(browser, prepared_item):
    """提交一条已准备的本地副本。

    职责：把副本交给已经连上的浏览器去提交。
    不在这里实现页面操作，也不判断成功或失败。
    """
    pass


def close_browser(browser):
    """关闭本机浏览器的调试连接，不关闭 Chrome 窗口。"""
    if browser is not None:
        browser.close()
