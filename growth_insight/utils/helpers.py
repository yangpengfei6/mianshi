"""通用工具函数。"""

import frappe
from frappe.utils import cint, getdate

from growth_insight.analytics.constants import PLAN_MRR


@frappe.whitelist()
def get_plan_default_mrr(plan: str) -> float:
    """返回某套餐的标准 MRR，供表单脚本自动带值。"""
    return PLAN_MRR.get(plan, 0.0)


def month_boundary(date_value, last: bool = False):
    """返回某日期所在月的第一天 / 最后一天。"""
    from growth_insight.analytics.core import month_end, month_start

    reference = getdate(date_value)
    return month_end(reference) if last else month_start(reference)


def chunked(sequence, size: int = 100):
    """分批迭代器，用于避免一次性处理过多数据把连接拖垮。"""
    bucket: list = []
    for item in sequence:
        bucket.append(item)
        if len(bucket) >= size:
            yield bucket
            bucket = []
    if bucket:
        yield bucket


def count_by(rows, fieldname: str) -> dict:
    """按字段计数，用于快速自检。"""
    result: dict = {}
    for row in rows:
        key = cint(row.get(fieldname))
        result[key] = result.get(key, 0) + 1
    return result
