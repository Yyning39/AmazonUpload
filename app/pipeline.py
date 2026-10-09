"""按固定顺序串联各阶段。"""

from pathlib import Path

from app.browser import close_browser, open_imaging_manage, port_for, return_to_imaging_manage, switch_site
from app.excel_io import read_workbook
from app.imaging_upload import upload_images
from app.queue_store import close_store, enqueue, list_queue, open_store, update_status

# plan.md 第 6 节：图片在这个根目录下按关键词和国家代码分文件夹。
PICTURE_ROOT = Path(r"D:\aosom\data\picture")


def upload_from_excel(excel_path, picture_root, database_path, log_path=None, timeout=30):
    """Excel 入库后再上传。不回写 Excel。

    country 决定 Chrome 端口和图片管理网址。market 决定下拉里的站点，
    也决定图片目录里的国家代码和页面上的国家模块。
    关键词有 SKU 用 SKU，否则用 ASIN。
    每次上传后，经该国调试端口把每个框的变化追加到 log_path。
    """
    rows = read_workbook(excel_path)
    store = open_store(database_path)
    results = []
    try:
        start = len(list_queue(store))
        enqueue(store, rows)
        queued = list_queue(store)[start:]
        for row in queued:
            results.append(_upload_queued_row(store, row, picture_root, log_path, timeout))
        queued = [item for item in list_queue(store) if item["id"] >= queued[0]["id"]] if queued else []
    finally:
        close_store(store)
    return {"excel": rows, "queued": queued, "results": results}


def _upload_queued_row(store, row, picture_root, log_path, timeout):
    if not row["picture"]:
        update_status(store, row["id"], "跳过")
        return _row_result(row, "跳过", "不上传图片")
    keyword = str(row["sku"] or "").strip() or str(row["asin"] or "").strip()
    session = None
    try:
        session = open_imaging_manage(row["country"], timeout=timeout)
        switch_site(session, row["market"], timeout=timeout)
        return_to_imaging_manage(session, row["country"], timeout=timeout)
        slots = upload_images(
            session,
            keyword,
            row["market"],
            picture_root,
            timeout=timeout,
            log_path=log_path,
            log_context={
                "port": port_for(row["country"]),
                "keyword": keyword,
                "country": row["country"],
                "market": row["market"],
            },
        )
    except Exception as exc:
        update_status(store, row["id"], "失败")
        return _row_result(row, "失败", str(exc))
    finally:
        close_browser(session)
    failed = [item for item in slots if item.get("status") != "成功"]
    if not slots:
        update_status(store, row["id"], "失败")
        return _row_result(row, "失败", "没有读到数字图片", slots)
    if failed:
        update_status(store, row["id"], "失败")
        return _row_result(row, "失败", failed[0].get("reason") or "有图片未成功", slots)
    update_status(store, row["id"], "完成")
    return _row_result(row, "完成", "", slots)


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


def run(excel_path, database_path, log_path, picture_root=None, timeout=30):
    """窗口选中的 Excel 进入 upload_from_excel。

    由 upload_from_excel 读取、入库一次、按 country 打开网页、按 market 上传。不回写 Excel。
    """
    return upload_from_excel(
        excel_path,
        picture_root or PICTURE_ROOT,
        database_path,
        log_path,
        timeout,
    )
