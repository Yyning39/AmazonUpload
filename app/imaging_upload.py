"""在图片管理页按关键词搜索，并把本地图片逐位交给文件框。

不读 Excel，不写队列，不回写状态。会点页面上的上传按钮来装上处理函数，但会挡住文件框自己的 click，因此不弹出系统文件框。
"""

import json
import time
from pathlib import Path

from app.browser import country_code
from app.cdp import BrowserConnectionError


class NeedsManual(Exception):
    """页面元素找不到，或确认条件还没补充。"""


# 下面几项是在已登录的西班牙图片管理页上看到的。
SEARCH_INPUT_SELECTOR = '[data-testid="search_input"] input[type="search"]'
PRODUCT_LIST_SELECTOR = ".infinite-scroll-component"
COUNTRY_SECTION_TESTID = "ou-section-{code}"
FILE_INPUT_SELECTOR = 'input[type="file"][data-testid="upload-image-input"]'
UPLOAD_BUTTON_SELECTOR = '[data-testid="upload-button"]'
SLOT_BOX_SELECTOR = '[data-testid="product-image-container"]'
# 空位是下拉框，上传后变成固定文字。找文件框时两种都认。
VARIANT_SELECTOR = '[data-testid="variant-dropdown"], [data-testid="variant-label"]'
VARIANT_LABEL_SELECTOR = '[data-testid="variant-label"]'
VARIANT_DROPDOWN_SELECTOR = '[data-testid="variant-dropdown"]'
# 等待图标的 class 带会变的后缀，只匹配 status-pending 这一段。
PENDING_ICON_SELECTOR = '[class*="status-pending"]'
NEWLY_UPLOADED_IMAGE_SELECTOR = '[data-testid="newly-uploaded-image"]'

# 目录文件名 → 页面位置名。01 对应 PT01，依次到 07。
SLOT_MAP = (
    ("MAIN", "MAIN"),
    ("01", "PT01"),
    ("02", "PT02"),
    ("03", "PT03"),
    ("04", "PT04"),
    ("05", "PT05"),
    ("06", "PT06"),
    ("07", "PT07"),
)
IMAGE_EXTENSIONS = {".gif", ".pjp", ".jfif", ".jpe", ".pjpeg", ".jpeg", ".jpg", ".png"}


def wait_for_element(session, selector, timeout=10, interval=0.25):
    """轮询直到 selector 存在且 offsetParent 不为空。

    间隔与原项目页面轮询一样，只使用 time.sleep。
    超时抛 NeedsManual，并写明 selector、耗时和 readyState。
    """
    _poll_until(session, selector, timeout, interval, require_ready=False)


def wait_for_ready(session, selector, timeout=10, interval=0.25):
    """先等到 readyState 为 interactive 或 complete，再等到标志元素出现。"""
    _poll_until(session, selector, timeout, interval, require_ready=True)


def _poll_until(session, selector, timeout, interval, require_ready):
    started = time.monotonic()
    deadline = started + float(timeout)
    last_state = "未知"
    while True:
        state = session.evaluate(_element_state_js(selector)) or {}
        last_state = state.get("readyState") or "未知"
        ready = last_state in {"interactive", "complete"}
        appeared = bool(state.get("present") and state.get("visible"))
        if appeared and (ready or not require_ready):
            return
        if time.monotonic() >= deadline:
            elapsed = time.monotonic() - started
            kind = "等待页面就绪超时" if require_ready else "等待元素超时"
            raise NeedsManual(
                f"{kind}：{selector}，等了 {elapsed:.1f} 秒，readyState={last_state}"
            )
        time.sleep(interval)


def _element_state_js(selector):
    return (
        "(() => { const el = document.querySelector("
        f"{json.dumps(selector)});"
        " return {readyState: document.readyState, present: !!el,"
        " visible: !!(el && el.offsetParent !== null)}; })()"
    )


def upload_images(session, keyword, country, picture_root, timeout=30):
    """搜索关键词，并把该国目录里的图片逐位上传。

    picture_root 是图片根目录。实际目录为
    picture_root/<关键词>/<国家代码>/。
    返回 MAIN、01…07 每个位置的结果。
    """
    code = country_code(country)
    text = str(keyword or "").strip()
    if not text:
        raise NeedsManual("关键词为空")
    directory = Path(picture_root) / text / code
    _search(session, text, timeout)
    _reveal_country(session, code, timeout)
    return _upload_slots(session, code, directory, timeout)


def _search(session, keyword, timeout):
    # 导航只等到图片管理标记。搜索框由前端稍后渲染，这里再轮询。
    wait_for_ready(session, SEARCH_INPUT_SELECTOR, timeout=timeout)
    filled = session.evaluate(_fill_search_js(keyword))
    if not filled:
        raise NeedsManual("无法填写搜索框")
    if not session.evaluate(_press_enter_js()):
        raise NeedsManual("无法在搜索框回车")
    wait_for_ready(session, PRODUCT_LIST_SELECTOR, timeout=timeout)
    _wait_for_search(session, keyword, timeout)


def _wait_for_search(session, keyword, timeout):
    """等到搜索框下方只有 1 件商品，且该行 SKU 或 ASIN 等于关键词。"""
    started = time.monotonic()
    deadline = started + float(timeout)
    while True:
        state = session.evaluate(_search_state_js(keyword)) or {}
        count = int(state.get("count") or 0)
        if state.get("ok"):
            return
        if count > 1:
            raise NeedsManual(f"搜索结果不是 1 件商品，当前 {count} 件")
        if count == 1 and state.get("ids") and not state.get("matched"):
            raise NeedsManual("商品行的 SKU/ASIN 与关键词不一致")
        if time.monotonic() >= deadline:
            elapsed = time.monotonic() - started
            ready = session.evaluate("document.readyState") or "未知"
            raise NeedsManual(
                f"搜索结果未加载完成：{PRODUCT_LIST_SELECTOR}，等了 {elapsed:.1f} 秒，readyState={ready}"
            )
        time.sleep(0.25)


def _reveal_country(session, country, timeout):
    selector = _country_selector(country)
    wait_for_element(session, selector, timeout=timeout)
    session.evaluate(_scroll_into_view_js(selector))
    _wait_until(
        session,
        _visible_js(selector),
        timeout,
        "国家模块未进入可视区域",
        selector,
    )


def _upload_slots(session, country, directory, timeout):
    results = []
    stopped = ""
    for file_stem, page_slot in SLOT_MAP:
        if stopped:
            results.append(_result(file_stem, page_slot, "失败", stopped))
            continue
        if file_stem == "MAIN":
            results.append(_result(file_stem, page_slot, "跳过", "暂不上传"))
            continue
        try:
            path = find_slot_image(directory, file_stem)
        except NeedsManual as exc:
            results.append(_result(file_stem, page_slot, "失败", str(exc)))
            stopped = "上一位未确认成功，已停止"
            continue
        if path is None:
            results.append(_result(file_stem, page_slot, "跳过", "无文件"))
            continue
        try:
            _upload_one(session, country, page_slot, path, timeout)
        except (NeedsManual, BrowserConnectionError) as exc:
            results.append(_result(file_stem, page_slot, "失败", str(exc), path))
            stopped = "上一位未确认成功，已停止"
            continue
        results.append(_result(file_stem, page_slot, "成功", "", path))
    return results


def _upload_one(session, country, page_slot, path, timeout):
    selector = _country_selector(country)
    _wait_for_slot(session, selector, page_slot, timeout)
    count = session.evaluate(_slot_count_js(selector, page_slot))
    if count == 0:
        raise NeedsManual(f"找不到 {page_slot} 的文件框")
    if count != 1:
        raise NeedsManual(f"{page_slot} 的文件框不止一个")
    session.evaluate(_scroll_slot_js(selector, page_slot))
    arm_error = session.evaluate(_arm_upload_handler_js(selector, page_slot))
    if arm_error:
        raise NeedsManual(f"{page_slot} {arm_error}")
    session.set_file_input_files(_slot_input_js(selector, page_slot), str(path.resolve()))
    _wait_until(
        session,
        _slot_success_js(selector, page_slot),
        timeout,
        f"{page_slot} 上传未确认成功",
        NEWLY_UPLOADED_IMAGE_SELECTOR,
    )


def _wait_for_slot(session, section_selector, page_slot, timeout):
    """文件框经常是隐藏的，不能用 offsetParent 判断。轮询该位置的文件框数量。"""
    started = time.monotonic()
    deadline = started + float(timeout)
    while True:
        count = int(session.evaluate(_slot_count_js(section_selector, page_slot)) or 0)
        if count == 1:
            return
        if count > 1:
            raise NeedsManual(f"{page_slot} 的文件框不止一个")
        if time.monotonic() >= deadline:
            elapsed = time.monotonic() - started
            ready = session.evaluate("document.readyState") or "未知"
            raise NeedsManual(
                f"等待上传位超时：{page_slot} {FILE_INPUT_SELECTOR}，等了 {elapsed:.1f} 秒，readyState={ready}"
            )
        time.sleep(0.25)


def find_slot_image(directory, file_stem):
    """在目录中找该位置的唯一图片。没有则返回 None。"""
    folder = Path(directory)
    if not folder.is_dir():
        return None
    wanted = str(file_stem).upper()
    matches = [
        path for path in folder.iterdir()
        if path.is_file() and path.stem.upper() == wanted and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    if not matches:
        return None
    if len(matches) > 1:
        names = "、".join(path.name for path in matches)
        raise NeedsManual(f"{file_stem} 有多张图片：{names}")
    return matches[0]


def _wait_until(session, expression, timeout, failure, selector):
    started = time.monotonic()
    deadline = started + float(timeout)
    while True:
        ready = session.evaluate(
            "(() => { const state = document.readyState;"
            f" const ok = {expression};"
            " return (state === 'interactive' || state === 'complete') && !!ok; })()"
        )
        if ready:
            return
        if time.monotonic() >= deadline:
            elapsed = time.monotonic() - started
            state = session.evaluate("document.readyState") or "未知"
            raise NeedsManual(f"{failure}：{selector}，等了 {elapsed:.1f} 秒，readyState={state}")
        time.sleep(0.25)


def _result(file_stem, page_slot, status, reason, path=None):
    item = {"slot": file_stem, "page_slot": page_slot, "status": status, "reason": reason}
    if path is not None:
        item["file"] = str(path)
    return item


def _country_selector(country):
    testid = COUNTRY_SECTION_TESTID.format(code=country_code(country))
    return f'[data-testid="{testid}"]'


def _exists_js(selector):
    return f"!!document.querySelector({json.dumps(selector)})"


def _visible_js(selector):
    return (
        "(() => { const el = document.querySelector("
        f"{json.dumps(selector)}); if (!el) return false;"
        " const rect = el.getBoundingClientRect();"
        " return rect.width > 0 && rect.height > 0 && rect.bottom > 0"
        " && rect.top < window.innerHeight; })()"
    )


def _scroll_into_view_js(selector):
    return (
        "(() => { const el = document.querySelector("
        f"{json.dumps(selector)}); if (!el) return false;"
        " el.scrollIntoView({block:'center'}); return true; })()"
    )


def _fill_search_js(keyword):
    """填写方式参考原项目 ChromePage.set_value_target，目标改成图片页搜索框。"""
    selector = json.dumps(SEARCH_INPUT_SELECTOR)
    value = json.dumps(str(keyword))
    return f"""(() => {{
      const e = document.querySelector({selector});
      if (!(e instanceof HTMLInputElement)) return false;
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      setter.call(e, {value});
      e.dispatchEvent(new Event('input', {{bubbles:true, composed:true}}));
      e.dispatchEvent(new Event('change', {{bubbles:true, composed:true}}));
      return e.value === {value};
    }})()"""


def _press_enter_js():
    """回车方式参考原项目 ChromePage.press_enter_target。"""
    selector = json.dumps(SEARCH_INPUT_SELECTOR)
    return f"""(() => {{
      const e = document.querySelector({selector});
      if (!(e instanceof HTMLInputElement)) return false;
      const init = {{key:'Enter', code:'Enter', which:13, keyCode:13, bubbles:true, composed:true}};
      e.dispatchEvent(new KeyboardEvent('keydown', init));
      e.dispatchEvent(new KeyboardEvent('keyup', init));
      return true;
    }})()"""


def _slot_count_js(section_selector, page_slot):
    return f"({_slot_inputs_js(section_selector, page_slot)}).length"


def _scroll_slot_js(section_selector, page_slot):
    return f"""(() => {{
      const inputs = {_slot_inputs_js(section_selector, page_slot)};
      if (inputs.length !== 1) return false;
      inputs[0].scrollIntoView({{block:'center'}});
      return true;
    }})()"""


def _arm_upload_handler_js(section_selector, page_slot):
    """点这一格的上传按钮，让页面给文件框装上 onchange。

    按钮自己的处理会调用文件框的 click() 来打开系统文件框。
    调用期间换掉 HTMLInputElement.prototype.click，这次不会打开。
    装上之后返回空字符串；否则返回原因。
    """
    button = json.dumps(UPLOAD_BUTTON_SELECTOR)
    box = json.dumps(SLOT_BOX_SELECTOR)
    return f"""(() => {{
      const inputs = {_slot_inputs_js(section_selector, page_slot)};
      if (inputs.length !== 1) return '文件框不是一个';
      const input = inputs[0];
      const slot = input.closest({box});
      const button = slot && slot.querySelector({button});
      if (!button) return '没有上传按钮';
      const original = HTMLInputElement.prototype.click;
      HTMLInputElement.prototype.click = function() {{}};
      try {{
        button.click();
      }} finally {{
        HTMLInputElement.prototype.click = original;
      }}
      return typeof input.onchange === 'function' ? '' : '上传处理没有装上';
    }})()"""


def _slot_input_js(section_selector, page_slot):
    return f"""(() => {{
      const inputs = {_slot_inputs_js(section_selector, page_slot)};
      return inputs.length === 1 ? inputs[0] : null;
    }})()"""


def _search_state_js(keyword):
    """商品列表没有 data-testid，用页面上的 infinite-scroll-component。"""
    selector = json.dumps(PRODUCT_LIST_SELECTOR)
    wanted = json.dumps(str(keyword).strip().upper())
    return f"""(() => {{
      const ready = document.readyState === 'interactive' || document.readyState === 'complete';
      const list = document.querySelector({selector});
      const cards = list ? [...list.children] : [];
      const lines = ((cards[0]?.innerText) || '').split(/\\n/).map(item => item.trim()).filter(Boolean);
      const ids = [];
      for (let i = 0; i < lines.length; i += 1) {{
        const name = lines[i].toUpperCase();
        if (name === 'ASIN' || name === 'SKU') ids.push((lines[i + 1] || '').trim());
      }}
      const matched = ids.some(item => item.toUpperCase() === {wanted});
      return {{ok: ready && cards.length === 1 && matched, count: cards.length, matched, ids}};
    }})()"""


def _slot_success_js(section_selector, page_slot):
    """上传后的格子：新图片、固定位置名、等待图标同时出现。"""
    section = json.dumps(section_selector)
    box = json.dumps(SLOT_BOX_SELECTOR)
    label = json.dumps(VARIANT_LABEL_SELECTOR)
    menu = json.dumps(VARIANT_DROPDOWN_SELECTOR)
    pending = json.dumps(PENDING_ICON_SELECTOR)
    image = json.dumps(NEWLY_UPLOADED_IMAGE_SELECTOR)
    slot = json.dumps(page_slot)
    return f"""(() => {{
      const section = document.querySelector({section});
      if (!section) return false;
      const boxes = [...section.querySelectorAll({box})].filter(item => {{
        const fixed = item.querySelector({label});
        const text = (fixed?.innerText || '').trim().split(/\\s+/)[0];
        return text === {slot} && !item.querySelector({menu});
      }});
      if (boxes.length !== 1) return false;
      const item = boxes[0];
      return !!item.querySelector({image}) && !!item.querySelector({pending});
    }})()"""


def _slot_inputs_js(section_selector, page_slot):
    """只认文件框自己那一格。格子里必须恰好有一个位置名。"""
    section = json.dumps(section_selector)
    file_input = json.dumps(FILE_INPUT_SELECTOR)
    box = json.dumps(SLOT_BOX_SELECTOR)
    variant = json.dumps(VARIANT_SELECTOR)
    label = json.dumps(page_slot)
    return f"""(() => {{
      const section = document.querySelector({section});
      if (!section) return [];
      return [...section.querySelectorAll({file_input})].filter(input => {{
        const slot = input.closest({box});
        if (!slot) return false;
        const markers = [...slot.querySelectorAll({variant})];
        if (markers.length !== 1) return false;
        const text = (markers[0].innerText || '').trim().split(/\\s+/)[0];
        return text === {label};
      }});
    }})()"""
