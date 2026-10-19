"""在描述页按 ASIN 和 SKU 搜索，点进第一条匹配的商品描述，并提交以供批准。不读 Excel，不回写。"""

import json
import time
from urllib.parse import urlparse

from app.imaging_upload import NeedsManual


class AplusNotFound(Exception):
    """搜完仍没有可提交的 A+。调用方跳过这一侧，不算失败。"""


# 外层在页面上。真正的 input 在 kat-input 的 shadowRoot 里。
ASIN_INPUT_SELECTOR = 'kat-input[data-component-id="project-search-box"]'
# SKU 搜索在表格组件的 shadowRoot 里。part 不随语言变化。
DATA_GRID_SELECTOR = "kat-data-grid"
SKU_FORM_SELECTOR = ".search-form"
SKU_INPUT_SELECTOR = 'kat-input.search-input[part="search-input"]'
SEARCH_BUTTON_SELECTOR = 'kat-button.search-button[part="search-submit"]'
RESULT_ROW_SELECTOR = ".ag-center-cols-container .ag-row"
NAME_LINK_SELECTOR = '.ag-cell[col-id="name"] kat-link'
TYPE_CELL_SELECTOR = '.ag-cell[col-id="contentType"]'
CONTENT_STATUS_CELL_SELECTOR = '.ag-cell[col-id="contentStatus"]'
NEXT_PAGE_SELECTOR = '.ag-paging-button[ref="btNext"]'
PAGE_SUMMARY_SELECTOR = ".ag-paging-description"
CONTENT_TYPE_EBC = "EBC"
# 翻完所有结果页仍没有可点进去提交的描述。这是跳过，不是失败。
NO_UPLOADABLE_APLUS = "没有名称与 SKU 相同、内容类型为 EBC、内容状态为草稿的商品描述"
# 同一状态在中文和英文界面上的词。西班牙页上看到的是 Draft，不是「草稿」。
DRAFT_STATUSES = {"草稿", "draft"}
SUBMITTED_STATUSES = {"已提交", "submitted"}
SEARCH_ATTEMPTS = 3
SEARCH_WAIT_SECONDS = 60
# 提交弹层里的确定按钮。顶栏那颗是 submit-content-for-approval-button，不在这个弹层里。
APPROVE_BUTTON_SELECTOR = 'kat-modal[data-component-id="submit-content-modal"] kat-button[variant="primary"][size="base"][type="button"]'
STATUS_ROOT_SELECTOR = '[data-component-id="content-details-status"]'


def _is_draft(status):
    return str(status or "").strip().casefold() in DRAFT_STATUSES


def _is_submitted(status):
    return str(status or "").strip().casefold() in SUBMITTED_STATUSES


REVISION_STEPS = ("/edit", "/asins/edit", "/preview/edit", "/preview/edit/submit")


def search_description(session, asin, sku, timeout=30):
    """在描述页填写 ASIN、SKU，并点击 SKU 搜索框旁边的搜索按钮。

    返回点击前的列表记号，供后面判断搜索结果是否已经换过。不提交 A+。
    """
    asin_text = str(asin or "").strip()
    sku_text = str(sku or "").strip()
    if not asin_text:
        raise NeedsManual("ASIN 为空")
    if not sku_text:
        raise NeedsManual("SKU 为空")
    _wait_for_fields(session, timeout)
    if not session.evaluate(_fill_asin_js(asin_text)):
        raise NeedsManual("无法填写 ASIN 搜索框")
    if not session.evaluate(_fill_sku_js(sku_text)):
        raise NeedsManual("无法填写 SKU 搜索框")
    before = session.evaluate(_list_stamp_js()) or {}
    if not session.evaluate(_click_search_button_js()):
        raise NeedsManual("无法点击 SKU 搜索按钮")
    return before


def open_matched_description(session, sku, timeout=30, previous=None):
    """在搜索结果里逐行对比，点开第一条名称与 SKU 相同、内容类型为 EBC、内容状态为草稿的描述。

    搜索结果经常晚于点击出现。这里最多检索 3 次，每次最多等 60 秒。
    当前页没有就点下一页。不判断语言、ASIN。
    翻完仍没有可提交的描述时抛出 AplusNotFound，由调用方跳过，不回写。
    某一次已经确认没有匹配行时，后面几次若只是结果没再变化，仍按没有可提交的描述结束。
    """
    del timeout
    sku_text = str(sku or "").strip()
    if not sku_text:
        raise NeedsManual("SKU 为空")
    stamp = previous
    last_error = None
    last_missing = None
    for attempt in range(1, SEARCH_ATTEMPTS + 1):
        if attempt > 1:
            stamp = session.evaluate(_list_stamp_js()) or {}
            if not session.evaluate(_click_search_button_js()):
                raise NeedsManual("无法再次点击 SKU 搜索按钮")
        started = time.monotonic()
        deadline = started + SEARCH_WAIT_SECONDS
        try:
            _open_search_match(session, sku_text, started, deadline, stamp)
            return
        except AplusNotFound as exc:
            last_missing = exc
            last_error = exc
            if attempt == SEARCH_ATTEMPTS:
                raise
        except NeedsManual as exc:
            last_error = exc
            if attempt == SEARCH_ATTEMPTS or not _search_can_retry(exc):
                if last_missing is not None and _search_can_retry(exc):
                    raise last_missing
                raise
    if last_missing is not None:
        raise last_missing
    raise last_error


def _search_can_retry(exc):
    text = str(exc)
    return (
        text.startswith("等待描述搜索结果超时")
        or text.startswith("无法读取商品描述名称")
    )


def _open_search_match(session, sku_text, started, deadline, previous):
    _wait_for_search_results(session, started, deadline, previous)
    seen_pages = set()
    while True:
        state = _require_list(session, started, deadline)
        page_text = state.get("pageText") or ""
        rows = state.get("rows") or []
        if rows and all(not str(row.get("name") or "").strip() for row in rows):
            if time.monotonic() >= deadline:
                raise NeedsManual("无法读取商品描述名称")
            time.sleep(0.25)
            continue
        if page_text in seen_pages:
            raise NeedsManual(f"翻页后页码没有变化：{page_text}")
        seen_pages.add(page_text)
        if any(row.get("linked") and not row.get("name") for row in rows):
            raise NeedsManual("无法读取商品描述名称")
        matched = _first_match(rows, sku_text)
        if matched:
            href = matched.get("href") or ""
            if not session.evaluate(_click_name_link_js(href)):
                raise NeedsManual("无法点击商品描述名称")
            _wait_for_detail(session, started, deadline, href)
            return
        if not state.get("nextEnabled"):
            raise AplusNotFound(NO_UPLOADABLE_APLUS)
        if not session.evaluate(_click_next_page_js()):
            raise NeedsManual("无法点击下一页")
        _wait_for_page_change(session, started, deadline, page_text)


def _wait_for_fields(session, timeout):
    """等到 ASIN 框，以及表格里恰好一套 SKU 搜索框和搜索按钮。"""
    started = time.monotonic()
    deadline = started + float(timeout)
    while True:
        state = session.evaluate(_field_state_js()) or {}
        ready = state.get("readyState") in {"interactive", "complete"}
        if ready and state.get("ok"):
            return
        if int(state.get("forms") or 0) > 1 or int(state.get("asin") or 0) > 1:
            raise NeedsManual("描述页搜索框不止一套")
        if time.monotonic() >= deadline:
            elapsed = time.monotonic() - started
            ready_state = state.get("readyState") or "未知"
            raise NeedsManual(
                f"等待描述页搜索框超时：{ASIN_INPUT_SELECTOR}，等了 {elapsed:.1f} 秒，readyState={ready_state}"
            )
        time.sleep(0.25)


def _field_state_js():
    return f"""(() => {{
      const forms = {_search_forms_js()};
      const asin = document.querySelectorAll({json.dumps(ASIN_INPUT_SELECTOR)}).length;
      const form = forms.length === 1 ? forms[0] : null;
      const sku = form ? form.querySelectorAll({json.dumps(SKU_INPUT_SELECTOR)}).length : 0;
      const button = form ? form.querySelectorAll({json.dumps(SEARCH_BUTTON_SELECTOR)}).length : 0;
      return {{
        readyState: document.readyState,
        asin,
        forms: forms.length,
        sku,
        button,
        ok: asin === 1 && forms.length === 1 && sku === 1 && button === 1
      }};
    }})()"""


def _search_forms_js():
    """只认内部有搜索表单的那一个 kat-data-grid。"""
    grid = json.dumps(DATA_GRID_SELECTOR)
    form = json.dumps(SKU_FORM_SELECTOR)
    return f"""(() => {{
      return [...document.querySelectorAll({grid})].flatMap(item => {{
        if (!item.shadowRoot) return [];
        return [...item.shadowRoot.querySelectorAll({form})];
      }});
    }})()"""


def _fill_asin_js(asin):
    host = json.dumps(ASIN_INPUT_SELECTOR)
    value = json.dumps(asin)
    return f"""(() => {{
      const hosts = [...document.querySelectorAll({host})];
      if (hosts.length !== 1) return false;
      return {_fill_host_body("hosts[0]", value)};
    }})()"""


def _fill_sku_js(sku):
    host = json.dumps(SKU_INPUT_SELECTOR)
    value = json.dumps(sku)
    return f"""(() => {{
      const forms = {_search_forms_js()};
      if (forms.length !== 1) return false;
      const hosts = [...forms[0].querySelectorAll({host})];
      if (hosts.length !== 1) return false;
      return {_fill_host_body("hosts[0]", value)};
    }})()"""


def _fill_host_body(host_expr, value_literal):
    """把文字写进 kat-input 内部的 input。填写方式与图片页搜索框相同。"""
    return f"""(() => {{
      const host = {host_expr};
      const input = host.shadowRoot && host.shadowRoot.querySelector('input');
      if (!(input instanceof HTMLInputElement)) return false;
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      setter.call(input, {value_literal});
      if ('value' in host) host.value = {value_literal};
      input.dispatchEvent(new Event('input', {{bubbles:true, composed:true}}));
      input.dispatchEvent(new Event('change', {{bubbles:true, composed:true}}));
      return input.value === {value_literal};
    }})()"""


def _click_search_button_js():
    """只点击这一套搜索表单里、SKU 框旁边的搜索按钮。"""
    button = json.dumps(SEARCH_BUTTON_SELECTOR)
    return f"""(() => {{
      const forms = {_search_forms_js()};
      if (forms.length !== 1) return false;
      const hosts = [...forms[0].querySelectorAll({button})];
      if (hosts.length !== 1) return false;
      const inner = hosts[0].shadowRoot && hosts[0].shadowRoot.querySelector('button');
      if (!(inner instanceof HTMLButtonElement)) return false;
      inner.click();
      return true;
    }})()"""


def _first_match(rows, sku):
    """返回第一条名称与 SKU 完全相同、内容类型为 EBC、内容状态为草稿的行。"""
    for row in rows:
        if (
            row.get("name") == sku
            and row.get("type") == CONTENT_TYPE_EBC
            and _is_draft(row.get("status"))
        ):
            return row
    return None


def _require_list(session, started, deadline):
    """读当前这一页。超时说明等的是页码。"""
    while True:
        state = session.evaluate(_list_rows_js()) or {}
        ready = state.get("readyState") in {"interactive", "complete"}
        if ready and state.get("ok") and state.get("pageText") and state.get("overlayHidden"):
            return state
        if int(state.get("grids") or 0) > 1 or int(state.get("nextCount") or 0) > 1:
            raise NeedsManual("描述列表不止一套")
        if time.monotonic() >= deadline:
            ready_state = state.get("readyState") or "未知"
            raise NeedsManual(
                f"等待描述列表超时：{PAGE_SUMMARY_SELECTOR}，等了 {time.monotonic() - started:.1f} 秒，readyState={ready_state}"
            )
        time.sleep(0.25)


def _wait_for_search_results(session, started, deadline, previous):
    """等到列表页码、首行链接或行数和点击搜索前不一样，并且加载层已收起。"""
    previous = previous or {}
    while True:
        state = session.evaluate(_list_stamp_js()) or {}
        ready = state.get("readyState") in {"interactive", "complete"}
        changed = (
            state.get("pageText") != (previous.get("pageText") or "")
            or state.get("firstHref") != (previous.get("firstHref") or "")
            or state.get("rowCount") != previous.get("rowCount")
        )
        if ready and state.get("ok") and state.get("overlayHidden") and changed:
            return
        if int(state.get("grids") or 0) > 1:
            raise NeedsManual("描述列表不止一套")
        if time.monotonic() >= deadline:
            ready_state = state.get("readyState") or "未知"
            raise NeedsManual(
                f"等待描述搜索结果超时：{PAGE_SUMMARY_SELECTOR}，等了 {time.monotonic() - started:.1f} 秒，readyState={ready_state}"
            )
        time.sleep(0.25)


def _wait_for_page_change(session, started, deadline, previous_page):
    while True:
        state = session.evaluate(_list_stamp_js()) or {}
        ready = state.get("readyState") in {"interactive", "complete"}
        if (
            ready
            and state.get("ok")
            and state.get("overlayHidden")
            and state.get("pageText")
            and state.get("pageText") != previous_page
        ):
            return
        if time.monotonic() >= deadline:
            ready_state = state.get("readyState") or "未知"
            raise NeedsManual(
                f"等待下一页超时：{NEXT_PAGE_SELECTOR}，等了 {time.monotonic() - started:.1f} 秒，readyState={ready_state}"
            )
        time.sleep(0.25)


def _wait_for_detail(session, started, deadline, href):
    """点开后的网址变成这条描述的地址。不看标题文字。"""
    expected = urlparse(str(href or "")).path.rstrip("/")
    if not expected or expected == "/":
        raise NeedsManual("商品描述链接没有地址")
    while True:
        state = session.evaluate(_detail_location_js(expected)) or {}
        if state.get("ok"):
            return
        if time.monotonic() >= deadline:
            ready_state = state.get("readyState") or "未知"
            current = state.get("path") or "空"
            raise NeedsManual(
                f"等待商品描述详情超时：{expected}，等了 {time.monotonic() - started:.1f} 秒，readyState={ready_state}，当前路径={current}"
            )
        time.sleep(0.25)


def _list_stamp_js():
    summary = json.dumps(PAGE_SUMMARY_SELECTOR)
    return f"""(() => {{
      const grids = [...document.querySelectorAll({json.dumps(DATA_GRID_SELECTOR)})];
      if (grids.length !== 1) return {{ok:false, grids: grids.length, readyState: document.readyState}};
      const grid = grids[0];
      const overlay = grid.querySelector('.ag-overlay');
      const page = grid.querySelector({summary});
      const row = grid.querySelector({json.dumps(RESULT_ROW_SELECTOR + ' ' + NAME_LINK_SELECTOR)});
      return {{
        ok: true,
        grids: 1,
        overlayHidden: !!(overlay && overlay.classList.contains('ag-hidden')),
        pageText: page ? (page.innerText || '').replace(/\\s+/g, ' ').trim() : '',
        firstHref: row ? (row.getAttribute('href') || '') : '',
        rowCount: grid.querySelectorAll({json.dumps(RESULT_ROW_SELECTOR)}).length,
        readyState: document.readyState
      }};
    }})()"""


def _list_rows_js():
    summary = json.dumps(PAGE_SUMMARY_SELECTOR)
    next_button = json.dumps(NEXT_PAGE_SELECTOR)
    return f"""(() => {{
      const grids = [...document.querySelectorAll({json.dumps(DATA_GRID_SELECTOR)})];
      const readyState = document.readyState;
      if (grids.length !== 1) return {{ok:false, grids: grids.length, readyState}};
      const grid = grids[0];
      const overlay = grid.querySelector('.ag-overlay');
      const page = grid.querySelector({summary});
      const buttons = [...grid.querySelectorAll({next_button})];
      const rows = [...grid.querySelectorAll({json.dumps(RESULT_ROW_SELECTOR)})].map(row => {{
        const link = row.querySelector({json.dumps(NAME_LINK_SELECTOR)});
        const typeCell = row.querySelector({json.dumps(TYPE_CELL_SELECTOR)});
        const statusCell = row.querySelector({json.dumps(CONTENT_STATUS_CELL_SELECTOR)});
        const shadowText = link && link.shadowRoot ? (link.shadowRoot.textContent || '') : '';
        const labelText = link ? (link.getAttribute('label') || '') : '';
        return {{
          name: (labelText || shadowText).replace(/\\s+/g, ' ').trim(),
          type: ((typeCell && typeCell.innerText) || '').replace(/\\s+/g, ' ').trim(),
          status: ((statusCell && statusCell.innerText) || '').replace(/\\s+/g, ' ').trim(),
          href: link ? (link.getAttribute('href') || '') : '',
          linked: !!link
        }};
      }});
      const next = buttons.length === 1 ? buttons[0] : null;
      return {{
        ok: true,
        grids: 1,
        nextCount: buttons.length,
        overlayHidden: !!(overlay && overlay.classList.contains('ag-hidden')),
        pageText: page ? (page.innerText || '').replace(/\\s+/g, ' ').trim() : '',
        nextEnabled: !!(next && !next.classList.contains('ag-disabled')),
        rows,
        readyState
      }};
    }})()"""


def _click_next_page_js():
    button = json.dumps(NEXT_PAGE_SELECTOR)
    return f"""(() => {{
      const grids = [...document.querySelectorAll({json.dumps(DATA_GRID_SELECTOR)})];
      if (grids.length !== 1) return false;
      const buttons = [...grids[0].querySelectorAll({button})];
      if (buttons.length !== 1) return false;
      if (buttons[0].classList.contains('ag-disabled')) return false;
      buttons[0].click();
      return true;
    }})()"""


def _click_name_link_js(href):
    """只点名称列里这个地址对应的链接，不点 ASIN 列。"""
    target = json.dumps(href)
    return f"""(() => {{
      const grids = [...document.querySelectorAll({json.dumps(DATA_GRID_SELECTOR)})];
      if (grids.length !== 1) return false;
      const links = [...grids[0].querySelectorAll({json.dumps(NAME_LINK_SELECTOR)})].filter(link => {{
        return (link.getAttribute('href') || '') === {target};
      }});
      if (links.length !== 1) return false;
      const anchor = links[0].shadowRoot && links[0].shadowRoot.querySelector('a');
      if (!(anchor instanceof HTMLAnchorElement)) return false;
      anchor.click();
      return true;
    }})()"""


def _detail_location_js(expected):
    path = json.dumps(expected)
    return f"""(() => {{
      const current = location.pathname.replace(/\\/+$/, "") || "/";
      const ready = document.readyState === 'interactive' || document.readyState === 'complete';
      const wanted = {path};
      const ok = ready && (current === wanted || current.indexOf(wanted + "/") === 0);
      return {{ok, path: current, readyState: document.readyState}};
    }})()"""


def submit_for_approval(session, timeout=30):
    """从当前详情网址的修订编号出发，依次打开四个后缀，再点弹层里的提交按钮。

    按钮是提交弹层 submit-content-modal 里那颗 primary 按钮，不看按钮文字。
    成功标志是 content-details-status 里的状态值变为「已提交」。
    """
    href = _wait_for_revision_href(session, timeout)
    base = _revision_base(href)
    for suffix in REVISION_STEPS:
        _open_revision_step(session, base + suffix, timeout)
    _wait_for_approve_button(session, timeout)
    if not session.evaluate(_click_approve_button_js()):
        raise NeedsManual("无法点击提交以供批准")
    _wait_for_submitted(session, timeout)


def _wait_for_revision_href(session, timeout):
    """内容页会先停在 /content/编号，随后才跳到 /revision/修订号。等到修订号出现。"""
    started = time.monotonic()
    deadline = started + float(timeout)
    href = ""
    while True:
        href = session.evaluate("location.href") or ""
        if "/revision/" in (urlparse(str(href)).path or ""):
            return href
        if time.monotonic() >= deadline:
            raise NeedsManual("等待修订编号超时，当前网址：" + (href or "空"))
        time.sleep(0.25)


def _revision_base(href):
    """保留到 /revision/修订号。后面的 /edit、/asins/edit、/preview/edit 都去掉。"""
    parsed = urlparse(str(href or ""))
    path = (parsed.path or "").rstrip("/")
    for suffix in ("/preview/edit/submit", "/preview/edit", "/asins/edit", "/edit"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    marker = "/revision/"
    if marker not in path:
        raise NeedsManual("当前网址没有修订编号：" + (href or "空"))
    head, revision = path.split(marker, 1)
    revision_id = revision.strip("/")
    if not revision_id or "/" in revision_id:
        raise NeedsManual("当前网址的修订编号无法识别：" + (href or "空"))
    if not parsed.scheme or not parsed.netloc:
        raise NeedsManual("当前网址无法识别：" + (href or "空"))
    return f"{parsed.scheme}://{parsed.netloc}{head}{marker}{revision_id}"


def _open_revision_step(session, url, timeout):
    """打开这一步的网址，直到路径一致且页面已加载。"""
    result = session.call("Page.navigate", {"url": url})
    error_text = str((result or {}).get("errorText") or "").strip()
    if error_text:
        raise NeedsManual("页面导航被拒绝：" + error_text)
    started = time.monotonic()
    deadline = started + float(timeout)
    expected = urlparse(url).path.rstrip("/") or "/"
    while True:
        state = session.evaluate(_location_state_js()) or {}
        ready = state.get("readyState") in {"interactive", "complete"}
        if ready and str(state.get("path") or "") == expected:
            return
        if time.monotonic() >= deadline:
            ready_state = state.get("readyState") or "未知"
            current = state.get("href") or "空"
            raise NeedsManual(
                f"等待网址超时：{expected}，等了 {time.monotonic() - started:.1f} 秒，readyState={ready_state}，当前网址={current}"
            )
        time.sleep(0.25)


def _wait_for_approve_button(session, timeout):
    """只接受弹层里恰好一颗指定的提交按钮。"""
    started = time.monotonic()
    deadline = started + float(timeout)
    while True:
        state = session.evaluate(_approve_button_state_js()) or {}
        ready = state.get("readyState") in {"interactive", "complete"}
        count = int(state.get("count") or 0)
        if count > 1:
            raise NeedsManual(f"提交以供批准按钮不止一个：{APPROVE_BUTTON_SELECTOR}")
        if ready and count == 1:
            return
        if time.monotonic() >= deadline:
            ready_state = state.get("readyState") or "未知"
            raise NeedsManual(
                f"等待提交以供批准超时：{APPROVE_BUTTON_SELECTOR}，等了 {time.monotonic() - started:.1f} 秒，readyState={ready_state}"
            )
        time.sleep(0.25)


def _wait_for_submitted(session, timeout):
    """状态文字变成已提交才算提交成功。"""
    started = time.monotonic()
    deadline = started + float(timeout)
    while True:
        state = session.evaluate(_status_state_js()) or {}
        ready = state.get("readyState") in {"interactive", "complete"}
        labels = int(state.get("labels") or 0)
        paragraphs = int(state.get("paragraphs") or 0)
        if labels > 1:
            raise NeedsManual(f"状态栏不止一个：{STATUS_ROOT_SELECTOR}")
        if labels == 1 and paragraphs > 1:
            raise NeedsManual(f"状态值不止一处：{STATUS_ROOT_SELECTOR}")
        if ready and _is_submitted(state.get("text")):
            return
        if time.monotonic() >= deadline:
            ready_state = state.get("readyState") or "未知"
            current = state.get("text") or "空"
            raise NeedsManual(
                f"等待已提交超时：{STATUS_ROOT_SELECTOR}，等了 {time.monotonic() - started:.1f} 秒，readyState={ready_state}，当前状态={current}"
            )
        time.sleep(0.25)


def _location_state_js():
    return """(() => {
      const path = location.pathname.replace(/\\/+$/, "") || "/";
      return {href: location.href, path, readyState: document.readyState};
    })()"""


def _approve_button_state_js():
    selector = json.dumps(APPROVE_BUTTON_SELECTOR)
    return f"""(() => {{
      const buttons = [...document.querySelectorAll({selector})];
      return {{count: buttons.length, readyState: document.readyState}};
    }})()"""


def _click_approve_button_js():
    selector = json.dumps(APPROVE_BUTTON_SELECTOR)
    return f"""(() => {{
      const buttons = [...document.querySelectorAll({selector})];
      if (buttons.length !== 1) return false;
      const inner = buttons[0].shadowRoot && buttons[0].shadowRoot.querySelector('button');
      if (!(inner instanceof HTMLButtonElement)) return false;
      inner.click();
      return true;
    }})()"""


def _status_state_js():
    root = json.dumps(STATUS_ROOT_SELECTOR)
    return f"""(() => {{
      const readyState = document.readyState;
      const blocks = [...document.querySelectorAll({root})];
      if (blocks.length !== 1) return {{labels: blocks.length, paragraphs: 0, text: '', readyState}};
      const paragraphs = [...blocks[0].children].filter(el => el.tagName === 'P');
      const text = paragraphs.length === 1 ? (paragraphs[0].textContent || '').replace(/\\s+/g, ' ').trim() : '';
      return {{labels: 1, paragraphs: paragraphs.length, text, readyState}};
    }})()"""
