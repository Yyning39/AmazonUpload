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
IMAGING_MANAGE_MARKER = '[data-testid="image-manager-tab"]'
# 描述页。完成标记是内容区的 ASIN 搜索框，不在通用顶栏上。
CONTENT_MANAGER_PATH = "/enhanced-content/content-manager"
DESCRIPTION_PAGE_MARKER = 'kat-input[placeholder="搜索 ASIN 或商品名称"]'
SITE_SWITCHER_ICON_SELECTOR = ".dropdown-account-switcher-header-trailing-icon"
SITE_SWITCHER_HEADER_SELECTOR = ".dropdown-account-switcher-header"
SITE_SWITCHER_LIST_SELECTOR = ".dropdown-account-switcher-list"
SITE_OPTION_SELECTOR = ".dropdown-account-switcher-list-item-indented"
SITE_CURRENT_LABEL_SELECTOR = ".dropdown-account-switcher-header-label-regional"
# 代码对应选项文字和旗帜 data-test。英国旗帜是 country-flag-GB。
# 美国、加拿大不在当前展开的 OutSunny Iberica 列表里，没有看到旗帜代码，不写入。
SITE_OPTIONS = {
    "UK": {"label": "英国", "flag": "GB"},
    "DE": {"label": "德国", "flag": "DE"},
    "FR": {"label": "法国", "flag": "FR"},
    "IT": {"label": "意大利", "flag": "IT"},
    "ES": {"label": "西班牙", "flag": "ES"},
}


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


def description_page_url(country):
    """返回该国描述页的完整网址。路径固定为 /enhanced-content/content-manager。"""
    return home_url(country) + CONTENT_MANAGER_PATH


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


def connect_country(country, timeout=30) -> PageSession:
    """拉起该国独立 Chrome，并连接上面已打开的 Seller Central 页面。

    端口已在监听时复用现有窗口，不另开第二个进程。
    不打开图片页，也不打开描述页。选站点前用这一步。
    """
    return _connect_country_page(country, timeout=min(float(timeout), 20))


def switch_to_description_page(session, country, timeout=30):
    """切换到描述页。

    在已连接的页面上打开该 country 的 /enhanced-content/content-manager。
    成功时网址正确、页面已加载，且 ASIN 搜索框已出现。
    不填写，不提交。
    """
    _navigate_and_wait(
        session,
        description_page_url(country),
        timeout,
        DESCRIPTION_PAGE_MARKER,
        "描述页标记",
    )


def return_to_imaging_manage(session, country, timeout=30):
    """在已连接的页面上打开该 country 的图片管理网址。

    切换站点后页面会离开图片管理页，上传前用这一步回到该国网址。
    """
    _navigate_and_wait(session, imaging_manage_url(country), timeout)


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
    """先拉起该国独立 Chrome，再连到 Seller Central 标签。没有时再用其他普通网页。"""
    launch_chrome(country)
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


def _navigate_and_wait(session, url, timeout, marker=None, marker_name="图片管理标记"):
    """发 Page.navigate，然后轮询可观测条件，直到就绪或超时。

    Page.navigate 的调用方式参考原项目 ChromePage.navigate。
    轮询 readyState 的方式参考原项目 open_upload_page。
    原项目在导航后有一段固定等待；这里不以那段等待作为成功条件。
    """
    ready_marker = IMAGING_MANAGE_MARKER if marker is None else marker
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
            f" marker: !!document.querySelector({json.dumps(ready_marker)})}}))()"
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
    appeared = "已出现" if last.get("marker") else "未出现"
    raise PageNavigationError(f"导航超时：readyState={ready}，{marker_name}{appeared}")


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


def switch_site(session, country, timeout=30):
    """在当前页展开账户下拉，并点选目标站点。

    不搜索，不上传，不回写。调用方传入已经连上的页面。
    点击方式参考原项目 ChromePage.click_text 的元素 click。
    等待间隔与本文件导航轮询相同，来源是原项目 open_upload_page，不用固定长等待当成功条件。
    """
    from app.imaging_upload import NeedsManual

    code = country_code(country)
    option = SITE_OPTIONS.get(code)
    if option is None:
        raise NeedsManual(f"当前展开的账户列表里没有 {code} 的站点选项")
    _wait_switcher_icon(session, timeout)
    if not session.evaluate(_click_switcher_js()):
        raise NeedsManual("无法点击站点展开图标")
    _wait_site_option(session, option["flag"], timeout)
    if not session.evaluate(_click_site_option_js(option["flag"])):
        raise NeedsManual(f"无法点击站点选项 {code}")
    _wait_site_label(session, option["label"], timeout)


def _wait_switcher_icon(session, timeout):
    from app.imaging_upload import NeedsManual

    started = time.monotonic()
    deadline = started + float(timeout)
    while True:
        state = session.evaluate(_switcher_icon_state_js()) or {}
        count = int(state.get("count") or 0)
        if count > 1:
            raise NeedsManual(f"站点展开图标不止一个：{SITE_SWITCHER_ICON_SELECTOR}")
        if count == 1 and state.get("visible"):
            return
        if time.monotonic() >= deadline:
            elapsed = time.monotonic() - started
            ready = session.evaluate("document.readyState") or "未知"
            raise NeedsManual(
                f"等待站点展开图标超时：{SITE_SWITCHER_ICON_SELECTOR}，等了 {elapsed:.1f} 秒，readyState={ready}"
            )
        time.sleep(0.25)


def _wait_site_option(session, flag, timeout):
    from app.imaging_upload import NeedsManual

    started = time.monotonic()
    deadline = started + float(timeout)
    selector = f'{SITE_OPTION_SELECTOR} [data-test="country-flag-{flag}"]'
    while True:
        count = int(session.evaluate(_site_option_count_js(flag)) or 0)
        if count > 1:
            raise NeedsManual(f"站点选项不止一个：{selector}")
        if count == 1:
            return
        if time.monotonic() >= deadline:
            elapsed = time.monotonic() - started
            ready = session.evaluate("document.readyState") or "未知"
            raise NeedsManual(
                f"等待站点选项超时：{selector}，等了 {elapsed:.1f} 秒，readyState={ready}"
            )
        time.sleep(0.25)


def _wait_site_label(session, label, timeout):
    from app.imaging_upload import NeedsManual

    started = time.monotonic()
    deadline = started + float(timeout)
    while True:
        if session.evaluate(_site_label_js(label)):
            return
        if time.monotonic() >= deadline:
            elapsed = time.monotonic() - started
            ready = session.evaluate("document.readyState") or "未知"
            current = session.evaluate(_site_label_text_js()) or ""
            raise NeedsManual(
                f"等待站点切换完成超时：{SITE_CURRENT_LABEL_SELECTOR}，等了 {elapsed:.1f} 秒，readyState={ready}，当前文字={current}"
            )
        time.sleep(0.25)


def _switcher_icon_state_js():
    selector = json.dumps(SITE_SWITCHER_ICON_SELECTOR)
    return f"""(() => {{
      const icons = [...document.querySelectorAll({selector})];
      const icon = icons.length === 1 ? icons[0] : null;
      return {{count: icons.length, visible: !!(icon && icon.offsetParent !== null)}};
    }})()"""


def _click_switcher_js():
    icon_selector = json.dumps(SITE_SWITCHER_ICON_SELECTOR)
    header_selector = json.dumps(SITE_SWITCHER_HEADER_SELECTOR)
    return f"""(() => {{
      const icons = [...document.querySelectorAll({icon_selector})];
      if (icons.length !== 1) return false;
      const header = icons[0].closest({header_selector});
      if (!header) return false;
      if (header.getAttribute('aria-expanded') === 'true') return true;
      header.click();
      return true;
    }})()"""


def _site_option_count_js(flag):
    list_selector = json.dumps(SITE_SWITCHER_LIST_SELECTOR)
    option_selector = json.dumps(SITE_OPTION_SELECTOR)
    flag_selector = json.dumps(f'[data-test="country-flag-{flag}"]')
    return f"""(() => {{
      const list = document.querySelector({list_selector});
      if (!list) return 0;
      return [...list.querySelectorAll({option_selector})].filter(item => item.querySelector({flag_selector})).length;
    }})()"""


def _click_site_option_js(flag):
    list_selector = json.dumps(SITE_SWITCHER_LIST_SELECTOR)
    option_selector = json.dumps(SITE_OPTION_SELECTOR)
    flag_selector = json.dumps(f'[data-test="country-flag-{flag}"]')
    return f"""(() => {{
      const list = document.querySelector({list_selector});
      if (!list) return false;
      const items = [...list.querySelectorAll({option_selector})].filter(item => item.querySelector({flag_selector}));
      if (items.length !== 1) return false;
      items[0].scrollIntoView({{block:'center'}});
      items[0].click();
      return true;
    }})()"""


def _site_label_js(label):
    selector = json.dumps(SITE_CURRENT_LABEL_SELECTOR)
    wanted = json.dumps(label)
    return f"""(() => {{
      const labels = [...document.querySelectorAll({selector})];
      if (labels.length !== 1) return false;
      const ready = document.readyState === 'interactive' || document.readyState === 'complete';
      return ready && (labels[0].innerText || '').trim() === {wanted};
    }})()"""


def _site_label_text_js():
    selector = json.dumps(SITE_CURRENT_LABEL_SELECTOR)
    return f"""(() => {{
      const labels = [...document.querySelectorAll({selector})];
      return labels.length === 1 ? (labels[0].innerText || '').trim() : '';
    }})()"""
