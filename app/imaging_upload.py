"""在图片管理页按关键词搜索，并把本地图片逐位交给文件框。

不读 Excel，不写队列，不回写状态。会点页面上的上传按钮来装上处理函数，但会挡住文件框自己的 click，因此不弹出系统文件框。
"""

import json
import time
from datetime import datetime
from pathlib import Path

from app.browser import country_code
from app.cdp import BrowserConnectionError


class NeedsManual(Exception):
    """页面元素找不到，或确认条件还没补充。"""


class UploadNotConfirmed(NeedsManual):
    """新图已经交给这一格，但成功标志没有出现。"""


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
# 页面上刚传完时是临时图。随后换成亚马逊图片地址，testid 变为 url-image。两种都算上传完成。
NEWLY_UPLOADED_IMAGE_SELECTOR = '[data-testid="newly-uploaded-image"]'
URL_IMAGE_SELECTOR = '[data-testid="url-image"]'
UPLOADED_IMAGE_SELECTOR = f"{NEWLY_UPLOADED_IMAGE_SELECTOR}, {URL_IMAGE_SELECTOR}"
DASHED_SLOT_SELECTOR = '[data-testid="image-slot-dashed"]'
PENDING_SELECTOR = '[class*="status-pending"]'

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


def upload_images(session, keyword, country, directory, timeout=30, log_path=None, log_context=None, on_progress=None, before_each=None):
    """搜索关键词，并把目录里的图片逐位上传。

    directory 是 picture/<国家代码>/<sku>/<站点代码>。
    country 决定页面上的站点模块。
    返回每张数字图片的结果。0 对应 MAIN，01 对应 PT01，02 对应 PT02，依次类推。
    不上传 FRONT、SIDE、BACK。
    """
    code = country_code(country)
    text = str(keyword or "").strip()
    if not text:
        raise NeedsManual("关键词为空")
    directory = Path(directory)
    if on_progress:
        on_progress("搜索商品")
    _search(session, text, timeout)
    if on_progress:
        on_progress("打开国家模块")
    _reveal_country(session, code, timeout)
    return _upload_slots(session, code, directory, timeout, log_path, log_context, on_progress, before_each)


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


def _upload_slots(session, country, directory, timeout, log_path=None, log_context=None, on_progress=None, before_each=None):
    """只处理文件夹里的数字图片。0 上传到 MAIN，01 上传到 PT01，缺号不前移。

    不上传 FRONT、SIDE、BACK。目标位置名在页面上不是恰好一个时，这一张记失败，不改传别的格子。
    没有对应文件的格子保持原样。有图时把新文件交给这一格的文件框覆盖，不先删原图。
    错误留在返回列表里，不写 Excel。
    """
    images = _numbered_images(directory)
    slots = session.evaluate(_ordered_slots_js(_country_selector(country))) or []
    if not isinstance(slots, list) or not slots or any(not str(name).strip() for name in slots):
        raise NeedsManual("国家模块的格子顺序不完整")
    results = []
    previous = _box_states(session, country)
    for number, path in images:
        file_stem = f"{number:02d}"
        page_slot = _page_slot_name(number)
        if before_each:
            before_each()
        if on_progress:
            on_progress(f"上传 {file_stem}")
        if not page_slot or page_slot in _SKIPPED_PAGE_SLOTS or slots.count(page_slot) != 1:
            item = _result(file_stem, page_slot, "失败", f"{page_slot or file_stem}位置图片待审核", path)
        else:
            item = _upload_or_record(session, country, page_slot, path, file_stem, timeout)
        current = _box_states(session, country)
        if log_path:
            _append_picture_log(log_path, log_context, item, previous, current)
        previous = current
        results.append(item)
        if on_progress:
            on_progress(f"{file_stem} {item.get('status', '')}")
    return results


def _upload_or_record(session, country, page_slot, path, file_stem, timeout):
    """把文件交给这一格的文件框。格子里已有图时直接覆盖，不点删除。"""
    try:
        _upload_one(session, country, page_slot, path, timeout)
    except UploadNotConfirmed:
        return _result(file_stem, page_slot, "失败", f"{page_slot}位置上传失败", path)
    except (NeedsManual, BrowserConnectionError):
        return _result(file_stem, page_slot, "失败", f"{page_slot}位置图片待审核", path)
    return _result(file_stem, page_slot, "成功", "", path)


def _upload_one(session, country, page_slot, path, timeout):
    selector = _country_selector(country)
    _wait_for_slot(session, selector, page_slot, timeout)
    count = session.evaluate(_slot_count_js(selector, page_slot))
    if count == 0:
        raise NeedsManual(f"找不到 {page_slot} 的文件框")
    if count != 1:
        raise NeedsManual(f"{page_slot} 的文件框不止一个")
    session.evaluate(_scroll_slot_js(selector, page_slot))
    before = session.evaluate(_slot_image_state_js(selector, page_slot)) or {}
    arm_error = session.evaluate(_arm_upload_handler_js(selector, page_slot))
    if arm_error:
        raise NeedsManual(f"{page_slot} {arm_error}")
    session.set_file_input_files(_slot_input_js(selector, page_slot), str(path.resolve()))
    try:
        _wait_until(
            session,
            _slot_success_js(selector, page_slot, before.get("src") or ""),
            timeout,
            f"{page_slot} 上传未确认成功",
            UPLOADED_IMAGE_SELECTOR,
        )
    except NeedsManual as exc:
        raise UploadNotConfirmed(str(exc)) from exc


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


# 这些位置名不接收本次图片。页面上若写成 FRNT，同样跳过。
_SKIPPED_PAGE_SLOTS = {"FRONT", "SIDE", "BACK", "FRNT"}


def _page_slot_name(number):
    """文件名序号对应的页面位置。0 是 MAIN，01 是 PT01，02 是 PT02。"""
    if number == 0:
        return "MAIN"
    if number >= 1:
        return f"PT{number:02d}"
    return ""


def _numbered_images(directory):
    """按文件名数字排序。01 是第 1 张。同一序号多张则报错。非数字文件名不读。"""
    folder = Path(directory)
    if not folder.is_dir():
        return []
    grouped = {}
    for path in folder.iterdir():
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        if not path.stem.isdigit():
            continue
        grouped.setdefault(int(path.stem), []).append(path)
    images = []
    for number in sorted(grouped):
        matches = grouped[number]
        if len(matches) > 1:
            names = "、".join(path.name for path in matches)
            raise NeedsManual(f"{number:02d} 有多张图片：{names}")
        images.append((number, matches[0]))
    return images


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


def _box_states(session, country):
    """经当前调试连接读取国家模块里每个框的可见状态。"""
    states = session.evaluate(_all_boxes_js(_country_selector(country))) or []
    return states if isinstance(states, list) else []


def _append_picture_log(path, context, item, before, after):
    """把这一张图片处理完之后，每个框相对上传前的变化追加到 picture_log.md。"""
    context = context or {}
    lines = [
        f"【记录时间】：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"监听端口：{context.get('port', '')}",
        f"关键词：{context.get('keyword', '')}",
        f"sku：{context.get('sku', '')}",
        f"asin：{context.get('asin', '')}",
        f"country：{context.get('country', '')}",
        f"market：{context.get('market', '')}",
        f"图片：{Path(item.get('file') or '').name} → 第 {item.get('slot', '')} 张，页面位置 {item.get('page_slot') or '无'}",
        f"结果：{item.get('status', '')} {item.get('reason', '')}".rstrip(),
        "各框变化：",
    ]
    count = max(len(before), len(after))
    if count == 0:
        lines.append("- 国家模块里没有读到格子")
    for index in range(count):
        old = before[index] if index < len(before) else None
        new = after[index] if index < len(after) else None
        lines.append(f"- {_box_change_line(index + 1, old, new)}")
    lines.append("")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def _box_change_line(index, before, after):
    if before is None and after is not None:
        return f"{index} {_box_text(after)}：新增"
    if after is None and before is not None:
        return f"{index} {_box_text(before)}：消失"
    if _same_box(before, after):
        return f"{index} {_box_text(after)}：无变化"
    return f"{index} {_box_text(before)} → {_box_text(after)}"


def _same_box(before, after):
    keys = ("name", "marker", "image", "src", "dashed", "pending")
    return all((before or {}).get(key) == (after or {}).get(key) for key in keys)


def _box_text(state):
    state = state or {}
    name = state.get("name") or "无位置名"
    marker = state.get("marker") or "无标记"
    image = state.get("image") or "无图"
    src = state.get("src") or "无地址"
    dashed = "虚线" if state.get("dashed") else "实位"
    pending = "审核中" if state.get("pending") else "无审核标记"
    return f"{name} / {marker} / {image} / {dashed} / {pending} / {src}"


def _all_boxes_js(section_selector):
    """读取国家模块每个框：位置名、标记、图片、虚线空位、审核标记。"""
    section = json.dumps(section_selector)
    box = json.dumps(SLOT_BOX_SELECTOR)
    variant = json.dumps(VARIANT_SELECTOR)
    dashed = json.dumps(DASHED_SLOT_SELECTOR)
    pending = json.dumps(PENDING_SELECTOR)
    return f"""(() => {{
      const section = document.querySelector({section});
      if (!section) return [];
      return [...section.querySelectorAll({box})].map(item => {{
        const markers = [...item.querySelectorAll({variant})];
        const marker = markers.length === 1 ? markers[0] : null;
        const img = item.querySelector('img');
        return {{
          name: marker ? (marker.innerText || '').trim().split(/\\s+/)[0] : '',
          marker: marker ? (marker.getAttribute('data-testid') || '') : '',
          image: img ? (img.getAttribute('data-testid') || '') : '',
          src: img ? (img.currentSrc || img.src || '') : '',
          dashed: !!item.querySelector({dashed}),
          pending: !!item.querySelector({pending})
        }};
      }});
    }})()"""


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
      const ids = lines.filter(item => item.toUpperCase() === {wanted});
      const matched = ids.length > 0;
      return {{ok: ready && cards.length === 1 && matched, count: cards.length, matched, ids}};
    }})()"""


def _ordered_slots_js(section_selector):
    """国家模块里格子的位置名，顺序与页面一致。某一格没有唯一位置名时该项为空。"""
    section = json.dumps(section_selector)
    box = json.dumps(SLOT_BOX_SELECTOR)
    variant = json.dumps(VARIANT_SELECTOR)
    return f"""(() => {{
      const section = document.querySelector({section});
      if (!section) return [];
      return [...section.querySelectorAll({box})].map(item => {{
        const markers = [...item.querySelectorAll({variant})];
        if (markers.length !== 1) return '';
        return (markers[0].innerText || '').trim().split(/\\s+/)[0];
      }});
    }})()"""


def _slot_image_state_js(section_selector, page_slot):
    """这一格里现有图片的 testid 和地址。没有图片时两项都是空字符串。"""
    section = json.dumps(section_selector)
    box = json.dumps(SLOT_BOX_SELECTOR)
    variant = json.dumps(VARIANT_SELECTOR)
    slot = json.dumps(page_slot)
    return f"""(() => {{
      const section = document.querySelector({section});
      if (!section) return {{testid:'', src:''}};
      const boxes = [...section.querySelectorAll({box})].filter(item => {{
        const markers = [...item.querySelectorAll({variant})];
        if (markers.length !== 1) return false;
        const text = (markers[0].innerText || '').trim().split(/\\s+/)[0];
        return text === {slot};
      }});
      if (boxes.length !== 1) return {{testid:'', src:''}};
      const img = boxes[0].querySelector('img');
      if (!img) return {{testid:'', src:''}};
      return {{testid: img.getAttribute('data-testid') || '', src: img.currentSrc || img.src || ''}};
    }})()"""


def _slot_success_js(section_selector, page_slot, previous_src):
    """这一格的图片地址必须和上传前不同。

    空格子出现新地址即完成。已有图时，原地址还在就还没覆盖上。
    刚传完是 newly-uploaded-image，随后换成 url-image 的亚马逊地址也算完成。
    """
    section = json.dumps(section_selector)
    box = json.dumps(SLOT_BOX_SELECTOR)
    label = json.dumps(VARIANT_LABEL_SELECTOR)
    menu = json.dumps(VARIANT_DROPDOWN_SELECTOR)
    fresh = json.dumps(NEWLY_UPLOADED_IMAGE_SELECTOR)
    hosted = json.dumps(URL_IMAGE_SELECTOR)
    slot = json.dumps(page_slot)
    previous = json.dumps(previous_src or "")
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
      const img = item.querySelector({fresh}) || item.querySelector({hosted});
      const src = img ? (img.currentSrc || img.src || '') : '';
      return !!src && src !== {previous};
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
