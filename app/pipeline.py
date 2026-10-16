"""按固定顺序串联各阶段。"""

from pathlib import Path

from app.aplus_upload import open_matched_description, search_description, submit_for_approval
from app.browser import (
    close_browser,
    connect_country,
    port_for,
    return_to_imaging_manage,
    switch_site,
    switch_to_description_page,
)
from app.excel_io import read_workbook
from app.imaging_upload import upload_images
from app.picture_save import picture_directory, save_numbered_pictures
from app.queue_store import close_store, enqueue, list_queue, open_store, update_status

# 图片目录：D:\aosom\data\picture\<国家代码>\<sku>\<站点代码>
PICTURE_ROOT = Path(r"D:\aosom\data\picture")


def upload_from_excel(excel_path, picture_root, database_path, log_path=None, timeout=30, on_progress=None):
    """Excel 入库后按两列分别处理。不回写 Excel。

    先按 country 拉起独立 Chrome 并连接，再按 market 选站点。
    picture 为真时先按 src 把图片存成 01、02、03，再进入图片页上传。A+introduction 为真时切换到描述页，搜索后点进第一条匹配的商品描述，再提交到状态为已提交。
    图片失败不挡住描述页。两列都为假则跳过。
    """
    _notify(on_progress, "正在读取 Excel", 0, 0)
    rows = read_workbook(excel_path)
    store = open_store(database_path)
    results = []
    try:
        start = len(list_queue(store))
        enqueue(store, rows)
        queued = list_queue(store)[start:]
        total = len(queued)
        _notify(on_progress, f"共 {total} 行", 0, total)
        for index, row in enumerate(queued, 1):
            results.append(_upload_queued_row(store, row, picture_root, log_path, timeout, on_progress, index, total))
        queued = [item for item in list_queue(store) if item["id"] >= queued[0]["id"]] if queued else []
        _notify(on_progress, "全部处理完", total or 1, total or 1)
    finally:
        close_store(store)
    return {"excel": rows, "queued": queued, "results": results}


def _upload_queued_row(store, row, picture_root, log_path, timeout, on_progress=None, index=1, total=1):
    name = str(row["sku"] or "").strip() or str(row["asin"] or "").strip() or "未命名"
    place = f"{row['country']} / {row['market']}"

    def step(text, done=None):
        _notify(on_progress, f"第 {index}/{total} 行 {name} {place} {text}", index - 1 if done is None else done, total)

    if not row["picture"] and not row["A+introduction"]:
        update_status(store, row["id"], "跳过")
        step("跳过", index)
        return _row_result(row, "跳过", "图片：不上传；描述：不打开")
    keyword = str(row["sku"] or "").strip() or str(row["asin"] or "").strip()
    session = None
    picture_outcome = None
    description_outcome = None
    try:
        step("连接 Chrome")
        session = connect_country(row["country"], timeout=timeout)
        step("切换站点")
        switch_site(session, row["market"], timeout=timeout)
        if row["picture"]:
            step("上传图片")
            picture_outcome = _run_picture(session, row, keyword, picture_root, log_path, timeout, lambda text: step(text))
        if row["A+introduction"]:
            step("提交 A+")
            description_outcome = _run_description(session, row, timeout)
    except Exception as exc:
        update_status(store, row["id"], "失败")
        step("失败：" + str(exc).replace("\n", " ")[:120], index)
        return _row_result(row, "失败", str(exc))
    finally:
        close_browser(session)
    status, reason = _combine_outcome(row, picture_outcome, description_outcome)
    update_status(store, row["id"], status)
    step(status, index)
    slots = None if picture_outcome is None else picture_outcome.get("slots")
    return _row_result(row, status, reason, slots)


def _run_picture(session, row, keyword, picture_root, log_path, timeout, on_progress=None):
    """先按 src 把图片存成 01、02、03，再进入图片页上传。失败只记在这一边，不挡住描述页。"""
    try:
        folder = picture_directory(picture_root, row["country"], row["sku"], row["market"])
        save_numbered_pictures(folder, row["src"])
        return_to_imaging_manage(session, row["country"], timeout=timeout)
        slots = upload_images(
            session,
            keyword,
            row["market"],
            folder,
            timeout=timeout,
            log_path=log_path,
            on_progress=on_progress,
            log_context={
                "port": port_for(row["country"]),
                "keyword": keyword,
                "sku": row["sku"],
                "asin": row["asin"],
                "country": row["country"],
                "market": row["market"],
            },
        )
    except Exception as exc:
        return {"ok": False, "reason": str(exc), "slots": None}
    failed = [item for item in slots if item.get("status") != "成功"]
    if not slots:
        return {"ok": False, "reason": "没有读到数字图片", "slots": slots}
    if failed:
        return {"ok": False, "reason": failed[0].get("reason") or "有图片未成功", "slots": slots}
    return {"ok": True, "reason": "", "slots": slots}


def _run_description(session, row, timeout):
    """切换到描述页，搜索后点进匹配描述，再提交到状态为已提交。"""
    try:
        switch_to_description_page(session, row["country"], timeout=timeout)
        before = search_description(session, row["asin"], row["sku"], timeout=timeout)
        open_matched_description(session, row["sku"], timeout=timeout, previous=before)
        submit_for_approval(session, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
    return {"ok": True, "reason": ""}


def _combine_outcome(row, picture_outcome, description_outcome):
    """两列都判断。要求做的部分都成功才是完成。"""
    ok = True
    parts = []
    if row["picture"]:
        if picture_outcome and picture_outcome["ok"]:
            parts.append("图片：完成")
        else:
            ok = False
            reason = "" if picture_outcome is None else picture_outcome.get("reason") or "未完成"
            parts.append("图片：" + reason)
    else:
        parts.append("图片：不上传")
    if row["A+introduction"]:
        if description_outcome and description_outcome["ok"]:
            parts.append("描述：已提交")
        else:
            ok = False
            reason = "" if description_outcome is None else description_outcome.get("reason") or "未完成"
            parts.append("描述：" + reason)
    else:
        parts.append("描述：不打开")
    return ("完成" if ok else "失败", "；".join(parts))


def _row_result(row, status, reason, slots=None):
    item = {
        "sku": row["sku"],
        "asin": row["asin"],
        "country": row["country"],
        "market": row["market"],
        "status": status,
        "reason": reason,
    }
    if slots is not None:
        item["slots"] = slots
    return item


def run(excel_path, database_path, log_path, picture_root=None, timeout=30, on_progress=None):
    """窗口选中的 Excel 进入 upload_from_excel。

    先选站点。picture 为真做图片页，A+introduction 为真做描述页并提交。不回写 Excel。
    """
    return upload_from_excel(
        excel_path,
        picture_root or PICTURE_ROOT,
        database_path,
        log_path,
        timeout,
        on_progress,
    )


def _notify(on_progress, text, done, total):
    if on_progress is not None:
        on_progress(text, done, total)
