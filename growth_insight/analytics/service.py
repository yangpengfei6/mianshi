# -*- coding: utf-8 -*-
"""
Growth Insight - Frappe ORM 适配层

职责边界
--------
* 只做「数据库取数 → 归一化 → 调用 :mod:`growth_insight.analytics.core`」这一件事；
* 所有指标算法都在 core 里，**这里不允许出现任何聚合公式**，避免口径分裂；
* 处理 Frappe 特有的坑：

  - ``get_all`` 默认 ``limit_page_length=20``，取全量必须显式传 ``0``；
  - 多选过滤器传过来可能是 ``list`` / ``tuple`` / JSON 字符串，需统一归类；
  - 趋势分析不能用「筛选后的子集」计算期初活跃，否则期初数会失真。

两套数据口径（UI 上有标注）
--------------------------
* **趋势口径**：取 ``activation_date <= to_date`` 的**全量历史**，
  因为「期初活跃」必须看到更早签约且尚未流失的用户；
* **下钻口径**：取 ``from_date <= activation_date <= to_date`` 的**新增 cohort**，
  用于回答"这期间签约的这批人，来自哪些渠道/套餐/区域"。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date

import frappe
from frappe.utils import add_months, getdate

from growth_insight.analytics import constants as C
from growth_insight.analytics import core

DOCTYPE = "User Service Record"

#: 分析所需的最小字段集（保持精简，避免 SELECT *）
FIELDS = [
    "name",
    "user_id",
    "user_name",
    "region",
    "city",
    "channel",
    "device",
    "plan",
    "mrr",
    "status",
    "activation_date",
    "activation_type",
    "churn_date",
    "churn_reason",
    "lifetime_days",
]

DEFAULT_MONTHS = 24


# ---------------------------------------------------------------------------
# 过滤器处理
# ---------------------------------------------------------------------------
def as_list(value) -> list:
    """
    把多选过滤器的入参统一成 list。

    Script Report 的 ``MultiSelectList`` 在不同版本下会给出 list / tuple / JSON str，
    这里一次性兼容，避免前端传参形态差异导致 SQL 报错。
    """
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if item]
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [item for item in parsed if item]
            except ValueError:
                pass
        return [part.strip() for part in text.split(",") if part.strip()]
    return [value]


def resolve_filters(filters: dict | None = None) -> dict:
    """
    规范化报表/大屏过滤器。

    :return: dict，包含 ``from_date`` / ``to_date`` / ``as_of``（均为 ``date``）
        以及各维度多选列表。
    """
    filters = filters or {}
    today = getdate()

    to_date = getdate(filters.get("to_date")) if filters.get("to_date") else today
    from_raw = filters.get("from_date")
    if from_raw:
        from_date = getdate(from_raw)
    else:
        from_date = add_months(to_date, -DEFAULT_MONTHS)
    if from_date > to_date:
        from_date, to_date = to_date, from_date

    return {
        "from_date": from_date,
        "to_date": to_date,
        "as_of": to_date,
        "region": as_list(filters.get("region")),
        "channel": as_list(filters.get("channel")),
        "plan": as_list(filters.get("plan")),
        "device": as_list(filters.get("device")),
        "city": as_list(filters.get("city")),
        "status": as_list(filters.get("status")),
        "exclude_free": bool(filters.get("exclude_free")),
    }


def _build_db_filters(scope: dict, apply_window: bool) -> list:
    """构造 Frappe ORM 过滤器。"""
    conditions: list = [["docstatus", "!=", 2]]

    if apply_window:
        # 下钻口径：限定新增 cohort
        conditions.append(["activation_date", ">=", scope["from_date"]])
    # 两种口径都需要排除"尚未发生"的签约
    conditions.append(["activation_date", "<=", scope["to_date"]])

    for field in ("region", "channel", "plan", "device", "city", "status"):
        values = scope.get(field) or []
        if values:
            conditions.append([field, "in", tuple(values)])

    if scope.get("exclude_free"):
        conditions.append(["mrr", ">", 0])

    return conditions


def fetch_records(scope: dict, apply_window: bool = True) -> list[dict]:
    """
    取数并归一化。

    :param scope: :func:`resolve_filters` 的返回值
    :param apply_window: True 表示按下钻口径限定区间，False 表示取全量历史（趋势用）
    """
    rows = frappe.db.get_all(
        DOCTYPE,
        filters=_build_db_filters(scope, apply_window=apply_window),
        fields=FIELDS,
        order_by="activation_date asc",
        # 关键：get_all 默认只返回 20 条，取全量必须显式关闭分页
        limit_page_length=0,
        ignore_permissions=True,  # 报表/大屏自身已做角色鉴权，避免行级权限导致口径不一致
    )
    return core.normalize_records(rows)


# ---------------------------------------------------------------------------
# 对外数据装配
# ---------------------------------------------------------------------------
def get_analysis(scope: dict) -> dict:
    """
    一次性算好所有分析模型，供 Report 与 Dashboard 复用（保证两条出口口径一致）。

    :return: dict，包含 ``trend`` / ``cohort`` / ``retention`` / ``region`` /
        ``channel`` / ``plan`` / ``device`` / ``reasons`` / ``tenure`` /
        ``funnel`` / ``kpi`` / ``events``
    """
    trend_records = fetch_records(scope, apply_window=False)
    drill_records = fetch_records(scope, apply_window=True)
    as_of: date = scope["as_of"]

    months = core.month_sequence(scope["from_date"], scope["to_date"])

    return {
        "trend": core.growth_trend(trend_records, months=months, as_of=as_of),
        "retention": core.retention_matrix(drill_records, as_of=as_of, max_periods=12),
        "region": core.group_breakdown(drill_records, "region", as_of),
        "channel": core.group_breakdown(drill_records, "channel", as_of),
        "plan": core.group_breakdown(drill_records, "plan", as_of),
        "device": core.group_breakdown(drill_records, "device", as_of),
        "city": core.group_breakdown(drill_records, "city", as_of)[:15],
        "reasons": core.churn_reason_breakdown(drill_records),
        "tenure": core.tenure_distribution(drill_records, as_of),
        "funnel": core.value_funnel(drill_records, as_of),
        "kpi": core.snapshot(trend_records, as_of),
        "events": core.recent_events(trend_records, limit=30, as_of=as_of),
        "scope": {
            "from_date": scope["from_date"].isoformat(),
            "to_date": scope["to_date"].isoformat(),
            "record_count": len(drill_records),
            "trend_baseline_count": len(trend_records),
        },
    }


def get_dimension_options(fieldname: str) -> list[str]:
    """取某维度的实际取值，用于过滤器下拉。"""
    if fieldname not in C.DIMENSIONS:
        return []
    values = frappe.db.get_all(DOCTYPE, pluck=fieldname, distinct=True, limit_page_length=0)
    return [value for value in values if value]


def get_date_bounds() -> tuple[date, date]:
    """返回数据的自然边界，用于给过滤器提供默认区间。"""
    to_date = getdate()
    earliest = frappe.db.get_all(
        DOCTYPE, filters={"docstatus": ("!=", 2)}, order_by="activation_date asc",
        pluck="activation_date", limit_page_length=1,
    )
    from_date = getdate(earliest[0]) if earliest else add_months(to_date, -DEFAULT_MONTHS)
    return from_date, to_date


def clean_row(row: dict) -> dict:
    """把 ``date`` 对象转为字符串，用于 JSON 序列化（Frappe RPC 要求可序列化）。"""
    output = {}
    for key, value in row.items():
        output[key] = value.isoformat() if isinstance(value, date) else value
    return output


def clean_rows(rows: Sequence[dict]) -> list[dict]:
    return [clean_row(row) for row in rows]
