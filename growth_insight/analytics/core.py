# -*- coding: utf-8 -*-
"""
Growth Insight - 用户增长分析引擎（纯 Python，零 Frappe 依赖）

为什么把分析逻辑抽到这一层？
-----------------------------
1. **口径单一**：Script Report、大屏 Page、REST API 三条出口共用同一份聚合逻辑，
   杜绝"报表和大屏数字不一致"这种最容易被面试官挑出的问题。
2. **可测试**：不依赖数据库连接，可以直接 pytest 断言，CI 里秒级跑完。
3. **可替换**：数据源换成数仓/Insights 时，只需替换上层 Service，引擎不动。

输入：``list[dict]``，每条记录为一张"用户服务档案"，必需字段见 :func:`normalize_records`。
输出：全部为 JSON 可序列化的原生类型。

指标口径（与设计文档 docs/DESIGN.md 保持一致）
-------------------------------------------
* **期初活跃** = 截至上月最后一天仍处于服务中的用户数
* **新增 / 流失** = 开通日期 / 流失日期落在本月内的用户数
* **期末活跃** = 期初活跃 + 新增 - 流失（恒等式，单测已验证）
* **月度流失率** = 本月流失数 / 期初活跃数（SaaS 标准 churn rate）
* **MRR(月经常性收入)** = 期末活跃用户 MRR 之和
* **ARPU** = MRR / 期末活跃数
* **净收入留存 NRR** = (期初 MRR - 本月流失用户 MRR) / 期初 MRR
* **LTV** = ARPU × 平均生命周期(月)
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Sequence

from growth_insight.analytics.constants import (
    CHURN_REASONS,
    PLAN_FREE,
    STATUS_ACTIVE,
    STATUS_CHURNED,
    TENURE_BUCKETS,
)
__all__ = [
    "normalize_records",
    "parse_date",
    "month_key",
    "month_start",
    "month_end",
    "add_months",
    "month_sequence",
    "growth_trend",
    "retention_matrix",
    "group_breakdown",
    "churn_reason_breakdown",
    "tenure_distribution",
    "plan_mix",
    "value_funnel",
    "snapshot",
    "recent_events",
    "derive_insights",
    "pct",
    "safe_div",
    "round2",
]

DAY = 30.4368  # 平均月天数 (365.2425 / 12)


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def parse_date(value: Any) -> date | None:
    """把 str / datetime / date 统一转成 ``datetime.date``，空值返回 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return datetime.fromisoformat(text.replace("/", "-")).date()


def month_start(d: date) -> date:
    """返回所在月份的第一天。"""
    return d.replace(day=1)


def month_end(d: date) -> date:
    """返回所在月份的最后一天。"""
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def month_key(d: date) -> str:
    """返回 ``YYYY-MM`` 形式的月份键。"""
    return f"{d.year:04d}-{d.month:02d}"


def add_months(d: date, months: int) -> date:
    """月份加减，自动处理月末溢出（如 1月31日 + 1 月 = 2月28/29日）。"""
    index = d.month - 1 + months
    year = d.year + index // 12
    month = index % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def month_diff(end: date, start: date) -> int:
    """精确的日历月份差（``end - start``），避免用 ``days // 30`` 产生的跨月误差。"""
    return (end.year - start.year) * 12 + (end.month - start.month)


def month_sequence(start: date, end: date) -> list[str]:
    """生成闭区间内的月份序列 ``['2025-01', '2025-02', ...]``。"""
    if start > end:
        return []
    keys: list[str] = []
    cursor = month_start(start)
    last = month_start(end)
    while cursor <= last:
        keys.append(month_key(cursor))
        cursor = month_start(add_months(cursor, 1))
    return keys


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """安全的除法，分母为 0 时返回默认值。"""
    return numerator / denominator if denominator else default


def pct(numerator: float, denominator: float, digits: int = 1) -> float:
    """计算百分比（已 ×100），分母为 0 时返回 ``default``。"""
    return round(safe_div(numerator, denominator) * 100, digits)


def round2(value: float) -> float:
    """保留两位小数的浮点舍入。"""
    return round(float(value or 0), 2)


# ---------------------------------------------------------------------------
# 记录规范化
# ---------------------------------------------------------------------------
#: 规范化后每条记录包含的字段
RECORD_FIELDS = (
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
)


def normalize_records(records: Iterable[dict]) -> list[dict]:
    """
    将原始记录（日期可能是字符串 / Frappe date 对象）规范化为分析引擎的统一形态。

    * 日期统一为 ``datetime.date``
    * ``mrr`` 缺失时按套餐默认值推导
    * ``churn_date`` 缺失或大于 ``status != 已流失`` 时清空，避免脏数据污染统计
    * ``lifetime_days`` 自动补齐
    """
    normalized: list[dict] = []
    for raw in records or []:
        row = dict(raw)
        activation = parse_date(row.get("activation_date"))
        if activation is None:
            # 没有开通日期的记录无法参与任何时序分析，直接丢弃
            continue

        churn = parse_date(row.get("churn_date"))
        if churn is not None and churn < activation:
            # 异常数据：流失早于开通，视为无效流失
            churn = None

        mrr = row.get("mrr")
        mrr = 0.0 if mrr is None else float(mrr)

        # 状态是派生态：以「流失日期」这个硬事实为准推导
        status = row.get("status") or STATUS_ACTIVE
        churn_reason = row.get("churn_reason") or ""
        if churn:
            status = STATUS_CHURNED
        else:
            status = STATUS_ACTIVE
            # 未流失却残留流失原因，会污染"流失归因"报表，这里统一清掉
            churn_reason = ""

        lifetime = row.get("lifetime_days")
        if not lifetime:
            reference = churn or date.today()
            lifetime = max((reference - activation).days, 0)

        normalized.append(
            {
                "name": row.get("name") or row.get("user_id") or "",
                "user_id": row.get("user_id") or "",
                "user_name": row.get("user_name") or row.get("user_id") or "",
                "region": row.get("region") or "未知",
                "city": row.get("city") or "未知",
                "channel": row.get("channel") or "未知",
                "device": row.get("device") or "未知",
                "plan": row.get("plan") or PLAN_FREE,
                "mrr": mrr,
                "status": status,
                "activation_date": activation,
                "activation_type": row.get("activation_type") or "首次开通",
                "churn_date": churn,
                "churn_reason": churn_reason,
                "lifetime_days": int(lifetime),
            }
        )
    return normalized


# ---------------------------------------------------------------------------
# 判定谓词
# ---------------------------------------------------------------------------
def _opened_in(record: dict, start: date, end: date) -> bool:
    return start <= record["activation_date"] <= end


def _churned_in(record: dict, start: date, end: date) -> bool:
    churn = record["churn_date"]
    return bool(churn and start <= churn <= end)


def _active_at(record: dict, day: date) -> bool:
    """判断用户在 ``day`` 当天是否仍处于服务中。"""
    if record["activation_date"] > day:
        return False
    churn = record["churn_date"]
    return churn is None or churn > day


def _record_date(record: dict) -> date:
    return record["activation_date"]


# ---------------------------------------------------------------------------
# 核心模型 1：月度增长趋势
# ---------------------------------------------------------------------------
def growth_trend(
    records: Sequence[dict], months: Sequence[str] | None = None, as_of: date | None = None
) -> dict:
    """
    计算月度增长 / 流失 / MRR 趋势。

    :param records: 规范化后的记录（**必须是全量历史**，否则期初活跃会失真）
    :param months: 需要输出的 ``YYYY-MM`` 列表；为 None 时按数据跨度自动生成
    :param as_of: 数据截止日。传入后，末月会被标记 ``is_partial=True``
        —— 当前月份尚未走完，新增/流失都天然偏低，**不能直接与完整月份对比**，
        这是做增长看板最常见的陷阱之一。
    :return: dict，键为月份，值为该月指标
    """
    records = list(records)
    if not records:
        return {"months": [], "rows": []}

    earliest = min(_record_date(r) for r in records)
    latest_churn = max((r["churn_date"] for r in records if r["churn_date"]), default=None)
    latest_activation = max(_record_date(r) for r in records)
    latest = max([d for d in (latest_churn, latest_activation) if d])

    if months is None:
        months = month_sequence(month_start(earliest), month_end(latest))

    rows: list[dict] = []
    for key in months:
        year, month = (int(part) for part in key.split("-"))
        start = date(year, month, 1)
        end = month_end(start)
        prev_end = start - timedelta(days=1)

        opening_active = sum(1 for r in records if _active_at(r, prev_end))
        opening_mrr = sum(r["mrr"] for r in records if _active_at(r, prev_end))

        new_records = [r for r in records if _opened_in(r, start, end)]
        churned_records = [r for r in records if _churned_in(r, start, end)]

        closing_active = sum(1 for r in records if _active_at(r, end))
        closing_mrr = sum(r["mrr"] for r in records if _active_at(r, end))

        new_mrr = sum(r["mrr"] for r in new_records)
        churned_mrr = sum(r["mrr"] for r in churned_records)

        rows.append(
            {
                "month": key,
                "is_partial": bool(as_of and end > as_of),
                "opening_active": opening_active,
                "new": len(new_records),
                "churned": len(churned_records),
                "net": len(new_records) - len(churned_records),
                "closing_active": closing_active,
                "churn_rate": pct(len(churned_records), opening_active),
                "growth_rate": pct(len(new_records), opening_active),
                "opening_mrr": round2(opening_mrr),
                "new_mrr": round2(new_mrr),
                "churned_mrr": round2(churned_mrr),
                "net_mrr": round2(new_mrr - churned_mrr),
                "closing_mrr": round2(closing_mrr),
                "nrr": round(safe_div(opening_mrr - churned_mrr, opening_mrr) * 100, 1),
                "arpu": round2(safe_div(closing_mrr, closing_active)),
            }
        )

    return {"months": list(months), "rows": rows}


# ---------------------------------------------------------------------------
# 核心模型 2：Cohort 留存矩阵
# ---------------------------------------------------------------------------
def retention_matrix(
    records: Sequence[dict],
    as_of: date,
    max_periods: int = 12,
    max_cohorts: int = 15,
) -> dict:
    """
    按「开通月份」分组的留存矩阵（Cohort Retention）。

    ``M0`` 表示开通当月月末仍存活的比例，``M1`` 为次月月末，以此类推。
    观察窗口不足的单元格返回 ``None``（前端渲染为空格），**不用 0 填充以免误导**。

    :param as_of: 数据截止日，晚于该日期的月份视为尚未发生
    :param max_periods: 最长观察期数（月）
    :param max_cohorts: 最多展示多少个 cohort（保留最近的）
    """
    records = list(records)
    cohorts: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        cohorts[month_key(record["activation_date"])].append(record)

    ordered_keys = sorted(cohorts.keys())
    if len(ordered_keys) > max_cohorts:
        ordered_keys = ordered_keys[-max_cohorts:]

    matrix: list[dict] = []
    for key in ordered_keys:
        members = cohorts[key]
        cohort_start = date(*(int(part) for part in key.split("-")), day=1)
        row = {
            "cohort": key,
            "size": len(members),
            "values": [],
            "counts": [],
        }
        for period in range(max_periods + 1):
            period_start = month_start(add_months(cohort_start, period))
            period_end = month_end(period_start)
            if period_start > month_start(as_of):
                # 观察窗口未到，不做外推
                row["values"].append(None)
                row["counts"].append(None)
                continue
            alive = sum(1 for member in members if _active_at(member, min(period_end, as_of)))
            row["values"].append(round(safe_div(alive, len(members)) * 100, 1))
            row["counts"].append(alive)
        matrix.append(row)

    return {
        "periods": [f"M{index}" for index in range(max_periods + 1)],
        "matrix": matrix,
    }


# ---------------------------------------------------------------------------
# 核心模型 3：维度下钻
# ---------------------------------------------------------------------------
def group_breakdown(records: Sequence[dict], field: str, as_of: date) -> list[dict]:
    """
    按任意维度字段聚合：区域 / 渠道 / 套餐 / 终端 / 城市。

    返回每个分组的：累计开通、当前活跃、累计流失、流失率、MRR、ARPU、平均生命周期、LTV。
    """
    records = list(records)
    buckets: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        buckets[str(record.get(field) or "未知")].append(record)

    total_count = len(records) or 1
    rows: list[dict] = []
    for key, members in buckets.items():
        active = [r for r in members if not r["churn_date"]]
        churned = [r for r in members if r["churn_date"]]
        mrr = sum(r["mrr"] for r in active)
        avg_lifetime = safe_div(sum(r["lifetime_days"] for r in members), len(members))
        arpu = safe_div(mrr, len(active))
        rows.append(
            {
                field: key,
                "total": len(members),
                "active": len(active),
                "churned": len(churned),
                "share": pct(len(members), total_count),
                "churn_rate": pct(len(churned), len(members)),
                "mrr": round2(mrr),
                "arpu": round2(arpu),
                "avg_lifetime_days": round(avg_lifetime, 1),
                "avg_lifetime_months": round(avg_lifetime / DAY, 1),
                "ltv": round2(arpu * (avg_lifetime / DAY)),
            }
        )

    rows.sort(key=lambda row: row["total"], reverse=True)
    return rows


def churn_reason_breakdown(records: Sequence[dict]) -> list[dict]:
    """流失原因归因：只统计已流失人群。"""
    churned = [r for r in records if r["churn_date"]]
    buckets: dict[str, list[dict]] = defaultdict(list)
    for record in churned:
        buckets[str(record.get("churn_reason") or "未填写")].append(record)

    total = len(churned) or 1
    rows = []
    for reason in CHURN_REASONS:
        members = buckets.pop(reason, [])
        if not members:
            continue
        rows.append(
            {
                "reason": reason,
                "count": len(members),
                "share": pct(len(members), total),
                "avg_lifetime_days": round(
                    safe_div(sum(r["lifetime_days"] for r in members), len(members)), 1
                ),
                "lost_mrr": round2(sum(r["mrr"] for r in members)),
            }
        )
    # 兜底：字典之外的原因（自定义数据场景）
    for reason, members in buckets.items():
        rows.append(
            {
                "reason": reason,
                "count": len(members),
                "share": pct(len(members), total),
                "avg_lifetime_days": round(
                    safe_div(sum(r["lifetime_days"] for r in members), len(members)), 1
                ),
                "lost_mrr": round2(sum(r["mrr"] for r in members)),
            }
        )
    rows.sort(key=lambda row: row["count"], reverse=True)
    return rows


def tenure_distribution(records: Sequence[dict], as_of: date) -> list[dict]:
    """生命周期分布：流失用户用实际生命周期，在服用户统计到 ``as_of``。"""
    records = list(records)
    rows: list[dict] = []
    for label, low, high in TENURE_BUCKETS:
        rows.append({"bucket": label, "active": 0, "churned": 0, "mrr": 0.0, "lost_mrr": 0.0})

    for record in records:
        churn = record["churn_date"]
        if churn:
            rows[_bucket_index(record["lifetime_days"])]["churned"] += 1
            rows[_bucket_index(record["lifetime_days"])]["lost_mrr"] += record["mrr"]
        else:
            days = max((as_of - record["activation_date"]).days, 0)
            rows[_bucket_index(days)]["active"] += 1
            rows[_bucket_index(days)]["mrr"] += record["mrr"]

    for row in rows:
        row["mrr"] = round2(row["mrr"])
        row["lost_mrr"] = round2(row["lost_mrr"])
        row["total"] = row["active"] + row["churned"]
    return rows


def _bucket_index(days: int) -> int:
    for index, (_label, low, high) in enumerate(TENURE_BUCKETS):
        if low <= days <= high:
            return index
    return len(TENURE_BUCKETS) - 1


def plan_mix(records: Sequence[dict], as_of: date) -> list[dict]:
    """当前存活用户的套餐构成（用于大屏环形图）。"""
    active = [r for r in records if not r["churn_date"]]
    return [
        row
        for row in group_breakdown(active, "plan", as_of)
    ]


def value_funnel(records: Sequence[dict], as_of: date) -> list[dict]:
    """
    用户价值漏斗：累计开通 → 产生付费 → 当前在服 → 长期留存(≥180 天)。

    说明：真实场景第五层通常是" expansions / 二次采购"，此处用长期留存近似，
    用于快速观察"买进来 → 留下来"的转化率衰减。
    """
    records = list(records)
    baseline = len(records) or 1
    active = [r for r in records if not r["churn_date"]]
    # 注意口径顺序必须严格单调递减，否则不成其为漏斗。
    # 「付费」限定为**当前在服**中的付费用户，而非历史付费过的用户，
    # 否则历史付费数可能大于在服数，漏斗出现"回升"的荒谬形状。
    paying_active = [r for r in active if r["mrr"] > 0]
    loyal = [
        r
        for r in paying_active
        if max((as_of - r["activation_date"]).days, 0) >= 180
    ]
    stages = [
        ("累计开通", len(records)),
        ("当前在服", len(active)),
        ("付费在服", len(paying_active)),
        ("长期留存", len(loyal)),
    ]
    return [
        {"stage": stage, "count": count, "rate": pct(count, baseline)}
        for stage, count in stages
    ]


# ---------------------------------------------------------------------------
# 核心模型 4：全局快照
# ---------------------------------------------------------------------------
def snapshot(records: Sequence[dict], as_of: date, previous_window: int = 30) -> dict:
    """
    单点快照 KPI。用于大屏顶部指标卡。

    :param previous_window: 环比对比窗口天数
    """
    records = list(records)
    if not records:
        return {
            "as_of": as_of.isoformat(),
            "total_acquired": 0,
            "active_users": 0,
            "churned_users": 0,
            "total_churn_rate": 0.0,
            "mrr": 0.0,
            "arr": 0.0,
            "arpu": 0.0,
            "avg_lifetime_days": 0.0,
            "ltv": 0.0,
            "new_last_period": 0,
            "churned_last_period": 0,
            "net_last_period": 0,
        }

    active = [r for r in records if not r["churn_date"]]
    churned = [r for r in records if r["churn_date"]]
    mrr = sum(r["mrr"] for r in active)
    arpu = safe_div(mrr, len(active))
    avg_lifetime = safe_div(sum(r["lifetime_days"] for r in records), len(records))

    window_start = as_of - timedelta(days=previous_window)
    new_last = sum(1 for r in records if window_start < r["activation_date"] <= as_of)
    churned_last = sum(
        1 for r in records if r["churn_date"] and window_start < r["churn_date"] <= as_of
    )

    return {
        "as_of": as_of.isoformat(),
        "total_acquired": len(records),
        "active_users": len(active),
        "churned_users": len(churned),
        "total_churn_rate": pct(len(churned), len(records)),
        "mrr": round2(mrr),
        "arr": round2(mrr * 12),
        "arpu": round2(arpu),
        "avg_lifetime_days": round(avg_lifetime, 1),
        "avg_lifetime_months": round(avg_lifetime / DAY, 1),
        "ltv": round2(arpu * (avg_lifetime / DAY)),
        "new_last_period": new_last,
        "churned_last_period": churned_last,
        "net_last_period": new_last - churned_last,
    }


def recent_events(records: Sequence[dict], limit: int = 20, as_of: date | None = None) -> list[dict]:
    """
    构建"实时事件流"：从开通 / 流失两类事实里抽取最近发生的事件，倒序返回。
    """
    events: list[dict] = []
    for record in records:
        events.append(
            {
                "date": record["activation_date"],
                "type": "开通",
                "user_name": record["user_name"],
                "plan": record["plan"],
                "region": record["region"],
                "channel": record["channel"],
                "mrr": record["mrr"],
            }
        )
        if record["churn_date"]:
            events.append(
                {
                    "date": record["churn_date"],
                    "type": "流失",
                    "user_name": record["user_name"],
                    "plan": record["plan"],
                    "region": record["region"],
                    "channel": record["channel"],
                    "mrr": record["mrr"],
                }
            )
    if as_of:
        events = [event for event in events if event["date"] <= as_of]
    events.sort(key=lambda event: event["date"], reverse=True)
    for event in events[:limit]:
        event["date"] = event["date"].isoformat()
    return events[:limit]


# ---------------------------------------------------------------------------
# 核心模型 5：自动结论
# ---------------------------------------------------------------------------
def derive_insights(records: Sequence[dict], as_of: date) -> list[dict]:
    """
    把数据里最值得注意的结论**算出来**，而不是让人盯着图表自己去脑补。

    管理层看大屏真正想问的是"所以呢？我该做什么？"——一堆柱子回答不了这个问题。
    规则放在 core 里（而不是某个 Controller 或 JS），保证：
    * 报表 / 大屏 / API 拿到的结论完全一致；
    * 规则可以离线单测。

    :return: ``[{"type": "warning"|"info"|"success", "text": str}, ...]``，
        文本为**原始中文**，国际化由调用方负责（见 api/dashboard.py）。
    """
    records = list(records)
    insights: list[dict] = []
    if not records:
        return insights

    # 1) 渠道质量分化：流失率最高 vs 最低
    channels = group_breakdown(records, "channel", as_of)
    if len(channels) >= 2:
        worst = max(channels, key=lambda row: row["churn_rate"])
        best = min(channels, key=lambda row: row["churn_rate"])
        if worst["churn_rate"] > best["churn_rate"]:
            ratio = round(worst["churn_rate"] / max(best["churn_rate"], 0.1), 1)
            insights.append(
                {
                    "type": "warning",
                    "text": (
                        f"渠道「{worst['channel']}」流失率 {worst['churn_rate']}%，"
                        f"是「{best['channel']}」（{best['churn_rate']}%）的 {ratio} 倍；"
                        f"该渠道累计拉新 {worst['total']} 人，建议下调投放占比或改造落地承接环节"
                    ),
                }
            )

    # 2) 首要流失原因 + 典型存活时长
    reasons = churn_reason_breakdown(records)
    if reasons:
        top = reasons[0]
        insights.append(
            {
                "type": "info",
                "text": (
                    f"首要流失原因是「{top['reason']}」，占流失人群的 {top['share']}%，"
                    f"平均仅存活 {round(top['avg_lifetime_days'])} 天，"
                    f"累计带走 MRR {round2(top['lost_mrr'])} 元"
                ),
            }
        )

    # 3) 最近一个完整月的净增情况
    rows = [row for row in growth_trend(records, as_of=as_of)["rows"] if not row.get("is_partial")]
    if rows:
        recent = rows[-1]
        insights.append(
            {
                "type": "success" if recent["net"] > 0 else "warning",
                "text": (
                    f"{recent['month']} 净增 {recent['net']} 人"
                    f"（新增 {recent['new']} / 流失 {recent['churned']}），"
                    f"当月流失率 {recent['churn_rate']}%，NRR {recent['nrr']}%"
                ),
            }
        )

    # 4) 套餐结构：免费试用的转化/留存拖累
    plans = {row["plan"]: row for row in group_breakdown(records, "plan", as_of)}
    free = plans.get(PLAN_FREE)
    if free and free["churn_rate"] >= 25:
        insights.append(
            {
                "type": "warning",
                "text": (
                    f"免费试用群体流失率高达 {free['churn_rate']}%"
                    f"（{free['total']} 人中已流失 {free['churned']} 人），"
                    "建议缩短试用期或增加转正引导动作"
                ),
            }
        )

    return insights
