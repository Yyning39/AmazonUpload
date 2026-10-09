"""读取来源 Excel。表头有六列，不含状态。"""

from pathlib import Path

from openpyxl import load_workbook

# 与 SQLite 队列除 status 外的列同名。状态不在 Excel 中。
# 站点表头是 market，与 plan.md 一致。country 用来打开网址，market 用来选择站点。
EXCEL_COLUMNS = ("sku", "asin", "country", "market", "picture", "A+introduction")
BOOL_COLUMNS = ("picture", "A+introduction")

_TRUE = {"1", "true", "yes", "y"}
_FALSE = {"0", "false", "no", "n"}


def read_workbook(path):
    """读取第一个工作表，返回六列记录。

    picture 与 A+introduction 转为布尔值。
    不写入数据库，也不生成 status。
    """
    path = Path(path or "")
    if not str(path) or path == Path("."):
        raise ValueError("未配置 source_excel")
    if not path.is_file():
        raise ValueError("找不到 Excel：" + str(path))
    if path.suffix.lower() != ".xlsx":
        raise ValueError("请选择 .xlsx 文件")

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = workbook.worksheets[0].iter_rows(values_only=True)
        headers = [str(value or "").strip() for value in next(rows, ())]
        missing = [name for name in EXCEL_COLUMNS if _header_index(headers, name) is None]
        if missing:
            raise ValueError("缺少列：" + "、".join(missing))
        indexes = {name: _header_index(headers, name) for name in EXCEL_COLUMNS}
        records = []
        for row_number, row in enumerate(rows, 2):
            values = list(row)
            if not any(value is not None and str(value).strip() for value in values):
                continue
            record = {}
            for name in EXCEL_COLUMNS:
                cell = values[indexes[name]] if indexes[name] < len(values) else None
                if name in BOOL_COLUMNS:
                    record[name] = _as_bool(cell, row_number, name)
                else:
                    text = str(cell or "").strip()
                    if not text:
                        raise ValueError(f"第 {row_number} 行 {name} 不能为空")
                    record[name] = text
            records.append(record)
        return records
    finally:
        workbook.close()


def _as_bool(value, row_number, column):
    """接受 Excel 布尔值，以及 1/0、true/false。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value in (0, 1):
        return bool(int(value))
    text = str(value or "").strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"第 {row_number} 行 {column} 不是布尔值")


def _header_index(headers, name):
    """按 plan.md 的表头原名定位。站点列只认 market。"""
    if name in headers:
        return headers.index(name)
    return None
