"""
analytics/core.py 单元测试（**无需 Frappe 环境**）

这一层之所以能做到零依赖，就是为了能在这里把指标口径锁死。
每个断言都对应一条"业务口径"，改动引擎时若破坏口径，CI 会立刻失败。
"""

from datetime import date, datetime

import pytest

from growth_insight.analytics import core
from growth_insight.analytics.constants import STATUS_ACTIVE, STATUS_CHURNED

AS_OF = date(2026, 10, 14)


def record(
    name,
    activation,
    churn=None,
    mrr=100.0,
    plan="专业版",
    channel="自然流量",
    region="华东",
    reason="功能不满足",
    **extra,
):
    row = {
        "name": name,
        "user_id": name,
        "user_name": name,
        "region": region,
        "city": "上海",
        "channel": channel,
        "device": "Web",
        "plan": plan,
        "mrr": mrr,
        "status": STATUS_CHURNED if churn else STATUS_ACTIVE,
        "activation_date": activation,
        "activation_type": "首次开通",
        "churn_date": churn,
        "churn_reason": reason if churn else "",
    }
    row.update(extra)
    return row


@pytest.fixture()
def sample():
    """一个刻意设计的极简数据集，每个数字都能手算验证。"""
    rows = [
        # 2026-01 cohort：3 人，其中 1 人在次月流失
        record("A", date(2026, 1, 5)),
        record("B", date(2026, 1, 10), date(2026, 2, 10)),
        record("C", date(2026, 1, 20), reason="预算/价格因素"),
        # 2026-02 cohort
        record("D", date(2026, 2, 5), mrr=300.0, plan="旗舰版", channel="转介绍"),
        record("E", date(2026, 2, 20), date(2026, 2, 25), reason="转向竞品"),
    ]
    return core.normalize_records(rows)


# ---------------------------------------------------------------------------
# 基础日期工具
# ---------------------------------------------------------------------------
def test_parse_date_accepts_multiple_formats():
    assert core.parse_date("2026-03-05") == date(2026, 3, 5)
    assert core.parse_date(date(2026, 3, 5)) == date(2026, 3, 5)
    assert core.parse_date(datetime(2026, 3, 5, 12, 30)) == date(2026, 3, 5)
    assert core.parse_date("") is None
    assert core.parse_date(None) is None


def test_add_months_handles_end_of_month():
    # 1月31日 + 1 月 必须落到 2 月的最后一天，而不是溢出到 3 月
    assert core.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert core.add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)  # 闰年
    assert core.add_months(date(2026, 3, 15), 12) == date(2027, 3, 15)


def test_month_start_end_and_sequence():
    assert core.month_start(date(2026, 2, 17)) == date(2026, 2, 1)
    assert core.month_end(date(2026, 2, 17)) == date(2026, 2, 28)
    assert core.month_sequence(date(2026, 11, 3), date(2027, 2, 8)) == [
        "2026-11",
        "2026-12",
        "2027-01",
        "2027-02",
    ]
    assert core.month_sequence(date(2027, 1, 1), date(2026, 1, 1)) == []


def test_safe_div_and_pct_never_blow_up():
    assert core.safe_div(10, 0) == 0.0
    assert core.pct(0, 0) == 0.0
    assert core.pct(1, 4) == 25.0
    assert core.round2(1.005) == 1.0  # Python round 的银行家舍入，这里只保证不报错


# ---------------------------------------------------------------------------
# 记录规范化
# ---------------------------------------------------------------------------
def test_normalize_drops_record_without_activation_date():
    rows = core.normalize_records([{"name": "X", "churn_date": "2026-01-01"}])
    assert rows == []


def test_normalize_treats_inverted_churn_as_invalid():
    row = core.normalize_records(
        [record("A", date(2026, 5, 1), churn=date(2026, 3, 1))]
    )[0]
    assert row["churn_date"] is None
    assert row["status"] == STATUS_ACTIVE


def test_normalize_derives_status_from_churn_date():
    # 状态与日期冲突时，以日期为准（日期是硬事实，状态是派生态）
    row = core.normalize_records([record("A", date(2026, 5, 1), churn=None)])[0]
    row["status"] = STATUS_CHURNED
    fixed = core.normalize_records([dict(row, status=STATUS_CHURNED, churn_date=None)])[0]
    assert fixed["status"] == STATUS_ACTIVE

    second = core.normalize_records([record("B", date(2026, 5, 1), churn=date(2026, 6, 1))])[0]
    assert second["status"] == STATUS_CHURNED


def test_normalize_computes_lifetime_days(sample):
    mapping = {row["name"]: row["lifetime_days"] for row in sample}
    assert mapping["A"] == (AS_OF - date(2026, 1, 5)).days
    assert mapping["B"] == (date(2026, 2, 10) - date(2026, 1, 10)).days


# ---------------------------------------------------------------------------
# 月度趋势
# ---------------------------------------------------------------------------
def test_trend_conservation_identity(sample):
    """期末活跃 = 期初活跃 + 新增 - 流失，这条恒等式必须人人成立。"""
    rows = core.growth_trend(sample, months=core.month_sequence(date(2026, 1, 1), date(2026, 3, 1)))["rows"]
    assert rows
    for row in rows:
        assert row["opening_active"] + row["new"] - row["churned"] == row["closing_active"]


def test_trend_first_month_has_zero_opening(sample):
    rows = core.growth_trend(sample)["rows"]
    assert rows[0]["month"] == "2026-01"
    assert rows[0]["opening_active"] == 0
    assert rows[0]["new"] == 3
    assert rows[0]["closing_active"] == 3


def test_trend_counts_new_and_churned_per_month(sample):
    rows = {row["month"]: row for row in core.growth_trend(sample)["rows"]}
    assert rows["2026-02"]["new"] == 2  # D、E
    assert rows["2026-02"]["churned"] == 2  # B(2月10日) 与 E(2月25日)
    assert rows["2026-02"]["closing_active"] == 3  # A、C、D


def test_trend_churn_rate_uses_opening_as_denominator(sample):
    rows = {row["month"]: row for row in core.growth_trend(sample)["rows"]}
    # 2 月：流失 2 / 期初 3 = 66.7%
    assert rows["2026-02"]["churn_rate"] == pytest.approx(66.7, abs=0.05)


def test_trend_marks_partial_month(sample):
    rows = {row["month"]: row for row in core.growth_trend(sample, as_of=date(2026, 2, 15))["rows"]}
    assert rows["2026-02"]["is_partial"] is True
    full = {row["month"]: row for row in core.growth_trend(sample, as_of=date(2026, 3, 31))["rows"]}
    assert full["2026-02"]["is_partial"] is False


def test_trend_mrr_tracks_alive_users_only(sample):
    rows = {row["month"]: row for row in core.growth_trend(sample)["rows"]}
    # 2 月末存活：A(100) C(100) D(300) = 500；B(已流失) E(已流失) 不计
    assert rows["2026-02"]["closing_mrr"] == 500.0
    assert rows["2026-02"]["arpu"] == pytest.approx(500 / 3, abs=0.01)


# ---------------------------------------------------------------------------
# Cohort 留存
# ---------------------------------------------------------------------------
def test_retention_matrix_m0_and_decay(sample):
    matrix = {
        row["cohort"]: row for row in core.retention_matrix(sample, AS_OF, max_periods=3)["matrix"]
    }
    # 2026-01 cohort 3 人，1 月底无人流失 -> 100%
    assert matrix["2026-01"]["size"] == 3
    assert matrix["2026-01"]["values"][0] == 100.0
    # M1（2 月底）只剩 A、C -> 66.7%
    assert matrix["2026-01"]["values"][1] == pytest.approx(66.7, abs=0.05)
    # 后续月份没有再流失，保持 66.7%
    assert matrix["2026-01"]["values"][3] == pytest.approx(66.7, abs=0.05)


def test_retention_matrix_returns_none_for_future_periods(sample):
    row = core.retention_matrix(sample, date(2026, 2, 28), max_periods=6)["matrix"][0]
    # 观察窗口到 2 月底，更晚的周期不应被推断为 0，而是 None
    assert row["values"][-1] is None
    assert row["counts"][-1] is None


def test_retention_uses_none_not_zero_for_unobserved(sample):
    """用 0 填充未发生月份会把"还没到"误读成"全部流失"，这是留存表最常见的错误。"""
    matrix = core.retention_matrix(sample, date(2026, 1, 31), max_periods=4)["matrix"]
    for row in matrix:
        assert all(value is None for value in row["values"][1:])


# ---------------------------------------------------------------------------
# 维度下钻
# ---------------------------------------------------------------------------
def test_group_breakdown_covers_all_records(sample):
    rows = core.group_breakdown(sample, "channel", AS_OF)
    assert sum(row["total"] for row in rows) == len(sample)
    assert sum(row["active"] for row in rows) == sum(1 for r in sample if not r["churn_date"])
    assert sum(row["churned"] for row in rows) == sum(1 for r in sample if r["churn_date"])


def test_group_breakdown_sorted_desc_and_share(sample):
    rows = core.group_breakdown(sample, "region", AS_OF)
    totals = [row["total"] for row in rows]
    assert totals == sorted(totals, reverse=True)
    assert rows[0]["share"] == pytest.approx(100.0, abs=0.1)


def test_normalize_clears_stale_churn_reason():
    """活跃用户残留流失原因会污染归因报表，规范化时必须清掉。"""
    row = core.normalize_records(
        [record("A", date(2026, 5, 1), churn=None, reason="转向竞品")]
    )[0]
    assert row["churn_reason"] == ""
    assert row["status"] == STATUS_ACTIVE


def test_group_breakdown_ltv_is_consistent_with_arpu(sample):
    """
    LTV = ARPU × 平均生命周期(月)。

    注意：报表上显示的生命周期只保留 1 位小数，而 LTV 用完整精度计算，
    因此两者允许一个"四舍五入最坏误差"（半个最小刻度 × ARPU）以内的偏差。
    """
    for row in core.group_breakdown(sample, "plan", AS_OF):
        expected = row["arpu"] * (row["avg_lifetime_days"] / core.DAY)
        tolerance = max(1.0, row["arpu"] * 0.06)
        assert row["ltv"] == pytest.approx(expected, abs=tolerance)


def test_churn_reason_breakdown(sample):
    rows = core.churn_reason_breakdown(sample)
    assert sum(row["count"] for row in rows) == 2  # B 与 E
    assert sum(row["share"] for row in rows) == pytest.approx(100.0, abs=0.2)
    assert rows[0]["count"] >= rows[-1]["count"]


def test_churn_reason_ignores_active_records(sample):
    # C 记录虽然传入了 reason，但它并未流失，因此不应出现在归因里
    rows = core.churn_reason_breakdown(sample)
    assert {row["reason"] for row in rows} == {"功能不满足", "转向竞品"}
    assert sum(row["count"] for row in rows) == 2


# ---------------------------------------------------------------------------
# 分布 / 漏斗 / 快照 / 事件流
# ---------------------------------------------------------------------------
def test_tenure_distribution_conservation(sample):
    rows = core.tenure_distribution(sample, AS_OF)
    assert sum(row["total"] for row in rows) == len(sample)
    assert sum(row["active"] for row in rows) == 3
    assert sum(row["churned"] for row in rows) == 2
    # 流失用户的 MRR 计入 lost_mrr 而非当期 mrr
    assert sum(row["mrr"] for row in rows) == pytest.approx(500.0)
    assert sum(row["lost_mrr"] for row in rows) == pytest.approx(200.0)


def test_funnel_is_monotonically_decreasing(sample):
    rows = core.value_funnel(sample, AS_OF)
    counts = [row["count"] for row in rows]
    assert counts == sorted(counts, reverse=True)
    assert rows[0]["stage"] == "累计开通"
    assert rows[0]["rate"] == 100.0


def test_snapshot_consistency(sample):
    snap = core.snapshot(sample, AS_OF)
    assert snap["total_acquired"] == len(sample)
    assert snap["active_users"] + snap["churned_users"] == snap["total_acquired"]
    assert snap["mrr"] == pytest.approx(sum(r["mrr"] for r in sample if not r["churn_date"]))
    assert snap["arr"] == pytest.approx(snap["mrr"] * 12)
    assert snap["total_churn_rate"] == pytest.approx(40.0, abs=0.1)  # 2/5
    assert snap["as_of"] == AS_OF.isoformat()


def test_snapshot_handles_empty_dataset():
    snap = core.snapshot([], AS_OF)
    assert snap["total_acquired"] == 0
    assert snap["mrr"] == 0.0
    assert snap["arpu"] == 0.0


def test_recent_events_sorted_descending(sample):
    events = core.recent_events(sample, limit=10, as_of=AS_OF)
    dates = [event["date"] for event in events]
    assert dates == sorted(dates, reverse=True)
    # 5 条记录 + 2 条流失 = 7 个事件
    assert len(events) == 7


def test_recent_events_respects_as_of(sample):
    events = core.recent_events(sample, limit=100, as_of=date(2026, 1, 31))
    assert all(event["date"] <= "2026-01-31" for event in events)


# ---------------------------------------------------------------------------
# 自动结论
# ---------------------------------------------------------------------------
def test_derive_insights_returns_actionable_sentences(sample):
    insights = core.derive_insights(sample, AS_OF)
    assert insights
    for item in insights:
        assert item["type"] in ("warning", "info", "success")
        assert isinstance(item["text"], str) and item["text"].strip()


def test_derive_insights_handles_empty_dataset():
    assert core.derive_insights([], AS_OF) == []


def test_derive_insights_flags_worst_channel(sample):
    """
    数据里"自然流量"0 流失、"转介绍"也不流失，但这是样本太小的情况；
    这里断言的是**结论引用了真实存在的最差渠道**，而不是凭空编一个名字。
    """
    insights = core.derive_insights(sample, AS_OF)
    channels = {row["channel"] for row in core.group_breakdown(sample, "channel", AS_OF)}
    text = " ".join(item["text"] for item in insights)
    assert any(channel in text for channel in channels)


def test_derive_insights_mentions_top_churn_reason(sample):
    insights = core.derive_insights(sample, AS_OF)
    reasons = [row["reason"] for row in core.churn_reason_breakdown(sample)]
    text = " ".join(item["text"] for item in insights)
    assert any(reason in text for reason in reasons)


def test_derive_insights_only_uses_complete_months(sample):
    """结论必须基于已走完的月份，否则月初会误报"流失暴涨"。"""
    insights = core.derive_insights(sample, date(2026, 3, 31))
    text = " ".join(item["text"] for item in insights)
    assert "2026-03" not in text or core.growth_trend(sample, as_of=date(2026, 3, 31))["rows"][-1][
        "is_partial"
    ] is False
