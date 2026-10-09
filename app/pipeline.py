"""按固定顺序串联各阶段。"""

from app.browser import close_browser, connect_browser, submit_item
from app.excel_io import read_workbook
from app.prepare import prepare_copy
from app.queue_store import close_store, enqueue, list_queue, open_store
from app.writeback import write_status


def run(config):
    """Excel 五列入库并写入 status。副本、浏览器和回写仍是空函数。"""
    rows = read_workbook(config.get("source_excel"))
    store = open_store(config.get("database_file"))
    try:
        enqueue(store, rows)
        browser_config = config.get("browser") or {}
        for item in list_queue(store):
            prepared = prepare_copy(item, config.get("prepared_dir"))
            browser = connect_browser(browser_config.get("host"), browser_config.get("port"))
            submit_item(browser, prepared)
            write_status(config.get("source_excel"), item, item.get("status"))
            close_browser(browser)
    finally:
        close_store(store)
