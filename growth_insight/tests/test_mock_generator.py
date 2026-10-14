"""
mock/generator.py 单元测试（**无需 Frappe 环境**）

Mock 数据不是"随便随机"就行：它决定了报表能不能讲出可信的故事。
这里把数据必须满足的**不变式**固化下来，防止调参时把数据质量搞坏。
"""

from datetime import date, timedelta

import pytest

from growth_insight.analytics import core
from growth_insight.analytics.constants import (
    ACTIVATION_TYPES,
    CHANNELS,
    DEVICES,
    PLAN_MRR,
    PLANS,
    REGIONS,
    STATUS_ACTIVE,
    STATUS_CHURNED,
)
from growth_insight.mock.generator import MockConfig, generate_records, summarize

TODAY = date(2026, 10, 14)


@pytest.fixture(scope="module")
def records():
    return generate_records(today=TODAY)


@pytest.fixture(scope="module")
def normalized(records):
    return core.normalize_records(records)


# ---------------------------------------------------------------------------
# 确定性
# ---------------------------------------------------------------------------
def test_same_seed_produces_identical_dataset():
    """固定种子 => 任何人 install 后看到的数据完全一致，便于回归与排障。"""
    first = generate_records(today=TODAY)
    second = generate_records(today=TODAY)
    assert len(first) == len(second)
    for left, right in zip(first, second, strict=True):
        assert left["user_id"] == right["user_id"]
        assert left["activation_date"] == right["activation_date"]
        assert left["churn_date"] == right["churn_date"]
        assert left["plan"] == right["plan"]
        assert left["mrr"] == right["mrr"]


def test_different_seed_produces_different_dataset():
    first = generate_records(today=TODAY)
    other = generate_records(today=TODAY, config=MockConfig(seed=1234567))
    assert len(first) != len(other) or any(
        # strict=False：两条数据集长度本就可能不同，逐对比较到较短者结束即可
        left["churn_date"] != right["churn_date"] for left, right in zip(first, other, strict=False)
    )


# ---------------------------------------------------------------------------
# 时间维度不变式
# ---------------------------------------------------------------------------
def test_no_future_dates(records):
    for row in records:
        assert row["activation_date"] <= TODAY, row
        if row["churn_date"]:
            assert row["churn_date"] <= TODAY, row


def test_churn_never_precedes_activation(records):
    for row in records:
        if row["churn_date"]:
            assert row["churn_date"] > row["activation_date"], row


def test_activation_spans_expected_months(records):
    months = {core.month_key(row["activation_date"]) for row in records}
    # 覆盖近 24 个月
    assert len(months) >= 20
    window = set(core.month_sequence(core.add_months(TODAY, -23), TODAY))
    assert months <= window
    assert months == window, "每个月都应至少有一笔签约"


# ---------------------------------------------------------------------------
# 字段一致性
# ---------------------------------------------------------------------------
def test_status_matches_churn_date(records):
    for row in records:
        if row["churn_date"]:
            assert row["status"] == STATUS_CHURNED
        else:
            assert row["status"] == STATUS_ACTIVE


def test_lifetime_days_are_consistent(records):
    for row in records:
        reference = row["churn_date"] or TODAY
        assert row["lifetime_days"] == (reference - row["activation_date"]).days


def test_ltv_consistent_with_mrr_and_lifetime(records):
    for row in records:
        expected = round(row["mrr"] * (row["lifetime_days"] / 30.4368), 2)
        assert row["ltv"] == pytest.approx(expected, abs=0.05)


def test_dimension_values_are_within_domain(records):
    for row in records:
        assert row["region"] in REGIONS
        assert row["channel"] in CHANNELS
        assert row["plan"] in PLANS
        assert row["device"] in DEVICES
        assert row["activation_type"] in ACTIVATION_TYPES


def test_plan_mrr_is_plausible(records):
    for row in records:
        base = PLAN_MRR[row["plan"]]
        if base == 0:
            assert row["mrr"] == 0
        else:
            # 允许折扣 / 加购带来的浮动
            assert 0 < row["mrr"] <= base * 1.25
            assert row["mrr"] % 1 == 0


def test_free_plan_never_conflicts_with_paid_activation_type(records):
    """「试用转正 / 套餐升级」意味着已经付费，不可能还是免费试用套餐。"""
    for row in records:
        if row["activation_type"] in ("试用转正", "套餐升级"):
            assert row["plan"] != "免费试用", row


# ---------------------------------------------------------------------------
# 业务合理性
# ---------------------------------------------------------------------------
def test_churn_rate_is_in_realistic_band(normalized):
    snap = core.snapshot(normalized, TODAY)
    assert 5.0 <= snap["total_churn_rate"] <= 45.0
    assert snap["active_users"] > 0
    assert snap["mrr"] > 0


@pytest.mark.parametrize("dimension", ["channel", "plan"])
def test_channel_and_plan_show_quality_spread(normalized, dimension):
    """
    数据必须能讲出故事：不同渠道/套餐的流失率要有明显分层，
    否则"渠道质量分析"视图就是一堆没有信息量的相同数字。
    """
    rows = core.group_breakdown(normalized, dimension, TODAY)
    rates = [row["churn_rate"] for row in rows]
    assert max(rates) - min(rates) >= 10.0, f"{dimension} 流失率缺乏区分度: {rates}"


def test_paid_channels_churn_more_than_referral(normalized):
    """复现"买量用户质量偏低"这一真实规律，保证洞察结论成立。"""
    rows = {row["channel"]: row for row in core.group_breakdown(normalized, "channel", TODAY)}
    assert rows["付费广告"]["churn_rate"] > rows["转介绍"]["churn_rate"]


def test_high_tier_plans_churn_less_than_free(normalized):
    rows = {row["plan"]: row for row in core.group_breakdown(normalized, "plan", TODAY)}
    assert rows["免费试用"]["churn_rate"] > rows["旗舰版"]["churn_rate"]


def test_trend_conservation_holds_on_generated_data(normalized):
    rows = core.growth_trend(normalized, as_of=TODAY)["rows"]
    assert len(rows) >= 20
    for row in rows:
        assert row["opening_active"] + row["new"] - row["churned"] == row["closing_active"]


def test_net_growth_is_positive(normalized):
    """整体业务应该是增长的，否则"增长分析"就没什么可分析的了。"""
    snap = core.snapshot(normalized, TODAY)
    assert snap["new_last_period"] > snap["churned_last_period"]


def test_retention_matrix_has_observable_periods(normalized):
    matrix = core.retention_matrix(normalized, TODAY, max_periods=12)
    assert len(matrix["matrix"]) >= 10
    latest = matrix["matrix"][-1]
    assert latest["values"][0] is not None
    # M0 留存应当很高（当月基本不会流失）
    assert latest["values"][0] >= 80.0


# ---------------------------------------------------------------------------
# 子表流水
# ---------------------------------------------------------------------------
def test_status_history_is_ordered_and_bounded(records):
    for row in records:
        history = row.get("status_history") or []
        assert history, "每条档案都应有至少一条开通流水"
        dates = [event["event_date"] for event in history]
        assert dates == sorted(dates)
        assert dates[0] == row["activation_date"]
        for event in history:
            assert event["event_date"] >= row["activation_date"]
            assert event["event_date"] <= TODAY
            assert event["event_type"] in (
                "开通",
                "续费",
                "升级",
                "降级",
                "流失预警",
                "流失",
                "召回",
            )


def test_churned_records_end_with_churn_event(records):
    churned = [row for row in records if row["churn_date"]]
    assert churned
    for row in churned:
        history = row["status_history"]
        assert history[-1]["event_type"] == "流失"
        assert history[-1]["event_date"] == row["churn_date"]


# ---------------------------------------------------------------------------
# 摘要工具
# ---------------------------------------------------------------------------
def test_summarize_reports_expected_keys(normalized):
    summary = summarize(normalized)
    for key in ("总记录数", "活跃用户数", "流失用户数", "总流失率", "当前 MRR"):
        assert key in summary


def test_reactivation_creates_records_with_shared_identity(records):
    """回流用户应复用原 user_id，否则会重复计成一个新客户。"""
    ids = [row["user_id"] for row in records]
    duplicates = {identity for identity in ids if ids.count(identity) > 1}
    assert duplicates, "数据集应至少包含一组流失回流用户"
    reactivated = [row for row in records if row["activation_type"] == "流失回流"]
    assert reactivated
    for row in reactivated:
        assert row["user_id"] in duplicates


def test_reactivation_happens_after_previous_churn(records):
    by_date = sorted(records, key=lambda row: row["activation_date"])
    seen_churn = {}
    for row in by_date:
        if row["activation_type"] == "流失回流":
            assert row["user_id"] in seen_churn
            assert row["activation_date"] > seen_churn[row["user_id"]]
        if row["churn_date"]:
            previous = seen_churn.get(row["user_id"])
            if previous is None or row["churn_date"] > previous:
                seen_churn[row["user_id"]] = row["churn_date"]


def test_today_default_is_supported():
    rows = generate_records()
    assert rows
    assert all(row["activation_date"] <= date.today() + timedelta(days=0) for row in rows)
