"""
Growth Insight - Mock 数据生成器（纯 Python，零 Frappe 依赖）

设计原则
--------
**拒绝 ``random.uniform`` 一把梭**。真实的用户增长数据有明确的生成机制，
本项目按「事件驱动的生存模型」生成，让最终报表能讲出可信的业务故事：

1. **月度签约数**：基线 × 复合增长 × 季节性 × 营销活动脉冲 × 噪声，而非均匀分布；
2. **流失采用离散时间 hazard 模型**：逐月以条件流失概率推进，
   ``hazard = 基线 × 渠道系数 × 套餐系数 × 生命周期系数``；
3. **渠道漂移**：近半年"付费广告"投放占比抬升 —— 复现真实的
   "买量扩盘 → 低质用户涌入 → 整体流失率抬头"现象，报表才有的看；
4. **确定性**：固定随机种子，任何人 install 后看到的数据完全一致，便于回归测试。

用法::

    from datetime import date
    from growth_insight.mock.generator import generate_records
    records = generate_records(today=date(2026, 10, 14))
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from growth_insight.analytics.constants import (
    ACTIVATION_TYPES,
    CHANNELS,
    PLAN_MRR,
    REGION_CITIES,
    STATUS_ACTIVE,
    STATUS_CHURNED,
)
from growth_insight.analytics.core import add_months, month_diff, month_end, month_key, month_start

__all__ = ["MockConfig", "generate_records", "summarize"]

DEFAULT_SEED = 20261014

# --- 开通类型 ---
#: "流失回流"是一种**特殊来源**，只能由下面的回流复购流程产生。
#: 若让它出现在普通签约的随机权重里，凭空生成的全新用户也会被标成"回流"，
#: 导致「回流率」指标虚高——这是被单元测试抓出来的一个真实缺陷。
NEW_USER_TYPES = ACTIVATION_TYPES[:4]
NEW_USER_TYPE_WEIGHTS = [80.0, 6.0, 8.0, 6.0]
REACTIVATION_TYPE = ACTIVATION_TYPES[4]

#: 流失说明模板。真实系统里这类字段往往是空的（人工补录），
#: 所以只给约 45% 的流失用户填写，避免出现"每条都一模一样"的假数据感。
CHURN_NOTE_TEMPLATES = [
    "回访记录：客户反馈「{reason}」，已电话沟通，暂无回归意向。",
    "客服工单已归档，核心诉求未满足：{reason}。",
    "使用 {days} 天后流失，退出问卷填写：{reason}。",
    "NPS 低分用户，备注：{reason}。",
    "客户在 {date} 明确提出不再续费，原因：{reason}。",
]


# ---------------------------------------------------------------------------
# 业务参数（所有"魔法数字"集中在此，便于调参和答辩时解释）
# ---------------------------------------------------------------------------
@dataclass
class MockConfig:
    """Mock 数据的可调参数集合。"""

    seed: int = DEFAULT_SEED
    months: int = 24
    base_monthly_signups: int = 15
    monthly_growth_rate: float = 0.055
    base_monthly_churn_hazard: float = 0.026
    reactivation_rate: float = 0.06  # 流失用户中回流复购的比例
    include_status_history: bool = True

    region_weights: dict[str, float] = field(
        default_factory=lambda: {
            "华东": 24.0,
            "华南": 19.0,
            "华北": 18.0,
            "华中": 12.0,
            "西南": 10.0,
            "西北": 6.0,
            "东北": 6.0,
            "海外": 5.0,
        }
    )

    #: 渠道基准权重
    channel_weights: dict[str, float] = field(
        default_factory=lambda: {
            "自然流量": 20.0,
            "搜索引擎": 17.0,
            "内容营销": 15.0,
            "社交媒体": 14.0,
            "转介绍": 11.0,
            "线下活动": 9.0,
            "应用商店": 8.0,
            "付费广告": 6.0,
        }
    )
    #: 渠道漂移量（正数为近半年扩量）
    channel_drift: dict[str, float] = field(
        default_factory=lambda: {
            "自然流量": -3.0,
            "搜索引擎": -2.0,
            "内容营销": 0.0,
            "社交媒体": 4.0,
            "转介绍": 0.0,
            "线下活动": -2.0,
            "应用商店": 1.0,
            "付费广告": 14.0,
        }
    )

    plan_weights: dict[str, float] = field(
        default_factory=lambda: {
            "免费试用": 20.0,
            "基础版": 30.0,
            "专业版": 26.0,
            "旗舰版": 15.0,
            "企业版": 9.0,
        }
    )
    device_weights: dict[str, float] = field(
        default_factory=lambda: {
            "Web": 42.0,
            "移动端 App": 26.0,
            "小程序": 18.0,
            "桌面客户端": 9.0,
            "开放 API": 5.0,
        }
    )

    #: 渠道流失修正系数（>1 表示该渠道用户更容易流失）
    channel_churn_factor: dict[str, float] = field(
        default_factory=lambda: {
            "自然流量": 0.70,
            "搜索引擎": 0.95,
            "内容营销": 0.85,
            "社交媒体": 1.35,
            "转介绍": 0.55,
            "线下活动": 0.80,
            "应用商店": 1.15,
            "付费广告": 1.85,
        }
    )
    #: 套餐流失修正系数
    plan_churn_factor: dict[str, float] = field(
        default_factory=lambda: {
            "免费试用": 1.55,
            "基础版": 1.10,
            "专业版": 0.80,
            "旗舰版": 0.55,
            "企业版": 0.28,
        }
    )

    #: 季节性系数，键为自然月
    seasonality: dict[int, float] = field(
        default_factory=lambda: {
            1: 0.82,  # 春节淡季
            2: 0.90,
            3: 1.15,  # 开门红
            4: 1.05,
            5: 1.08,
            6: 1.18,  # 年中冲刺
            7: 0.98,
            8: 1.00,
            9: 1.12,  # 金九
            10: 0.95,
            11: 1.25,  # 双十一
            12: 1.15,  # 年末签约
        }
    )

    #: 营销活动脉冲：距当前月的月偏移 -> 强度
    campaign_pulses: dict[int, float] = field(
        default_factory=lambda: {8: 1.45, 3: 1.30, 18: 1.35}
    )


def _weighted_pick(rng: random.Random, weights: dict[str, float]) -> str:
    keys = list(weights.keys())
    return rng.choices(keys, weights=[max(weights[key], 0.0) for key in keys], k=1)[0]


def _tenure_churn_factor(months_elapsed: int) -> float:
    """
    生命周期流失修正：SaaS 典型的"早流失期"曲线 ——
    前 3 个月磨合期流失最高，跨过试用期后显著回落，老客户趋于稳定。
    """
    if months_elapsed <= 2:
        return 1.90
    if months_elapsed <= 5:
        return 1.15
    if months_elapsed <= 11:
        return 0.75
    if months_elapsed <= 23:
        return 0.50
    return 0.40


def _pick_churn_reason(rng: random.Random, plan: str, channel: str) -> str:
    """流失原因采样 —— 与套餐/渠道联动，保证归因结果有业务解释力。"""
    weights = {
        "预算/价格因素": 20.0,
        "功能不满足": 18.0,
        "使用体验问题": 14.0,
        "转向竞品": 16.0,
        "服务支持不足": 10.0,
        "业务调整/需求消失": 14.0,
        "其他原因": 8.0,
    }
    if plan in ("免费试用", "基础版"):
        weights["预算/价格因素"] += 22.0
        weights["功能不满足"] += 10.0
    if channel in ("付费广告", "社交媒体"):
        weights["业务调整/需求消失"] += 18.0
        weights["预算/价格因素"] += 10.0
    if plan in ("旗舰版", "企业版"):
        weights["服务支持不足"] += 24.0
        weights["转向竞品"] += 12.0
        weights["预算/价格因素"] -= 8.0
    return _weighted_pick(rng, weights)


def _random_day_in_month(rng: random.Random, month_first: date, today: date) -> date:
    """在给定月内随机取一天，且不得晚于 ``today``。"""
    last = min(month_end(month_first), today)
    span = (last - month_first).days
    return month_first + timedelta(days=rng.randint(0, span)) if span > 0 else last


def _month_channel_weights(rng: random.Random, config: MockConfig, offset: int) -> dict[str, float]:
    """
    计算某月的渠道权重：随月份推移向 ``channel_drift`` 方向线性漂移。

    :param offset: 距当前月的月偏移，越大表示时间越早
    """
    months_back = max(config.months - offset, 0)
    progress = min(months_back / max(config.months, 1), 1.0)
    weights = {}
    for channel in CHANNELS:
        base = config.channel_weights.get(channel, 1.0)
        drift = config.channel_drift.get(channel, 0.0)
        weights[channel] = max(base + drift * progress, 0.1)
    # 加一点扰动，避免各月分布过于规整
    return {key: value * (1 + rng.uniform(-0.12, 0.12)) for key, value in weights.items()}


def _mrr_for_plan(rng: random.Random, plan: str) -> float:
    """按套餐基准价生成 MRR，叠加折扣/加购抖动而非单点值。"""
    base = PLAN_MRR.get(plan, 0.0)
    if base <= 0:
        return 0.0
    jitter = rng.uniform(-0.08, 0.18)
    return round(base * (1 + jitter) / 10) * 10


def _build_status_history(rng: random.Random, record: dict, mrr: float) -> list[dict]:
    """生成子表事件流水：开通 →（可选续费/预警）→（可选流失）。"""
    plan = record["plan"]
    activation = record["activation_date"]
    churn_date = record["churn_date"]

    history: list[dict] = [
        {
            "event_date": activation,
            "event_type": "开通",
            "plan": plan,
            "mrr": round(mrr, 2),
            "remark": record.get("remarks_history") or f"通过「{record['channel']}」渠道开通 {plan}",
        }
    ]

    # 每 6 个月补一条续费记录
    cursor = activation
    horizon = churn_date or activation
    for _ in range(8):
        cursor = add_months(cursor, 6)
        if cursor > horizon:
            break
        history.append(
            {
                "event_date": cursor,
                "event_type": "续费",
                "plan": plan,
                "mrr": round(mrr, 2),
                "remark": "按期续费，服务周期顺延",
            }
        )

    # 流失前一个月留一条预警记录
    if churn_date:
        warning_date = add_months(churn_date, -1)
        if warning_date > activation:
            history.append(
                {
                    "event_date": warning_date,
                    "event_type": "流失预警",
                    "plan": plan,
                    "mrr": round(mrr, 2),
                    "remark": "活跃度连续下降，触发流失预警",
                }
            )
        history.append(
            {
                "event_date": churn_date,
                "event_type": "流失",
                "plan": plan,
                "mrr": 0.0,
                "remark": f"流失原因：{record.get('churn_reason') or '未填写'}",
            }
        )

    history.sort(key=lambda item: item["event_date"])
    return history


def _simulate_user(
    rng: random.Random,
    config: MockConfig,
    index: int,
    signup_date: date,
    today: date,
    activation_type: str,
) -> dict:
    """生成一条完整的用户服务档案（含流失模拟）。"""
    elapsed_months = month_diff(month_start(today), month_start(signup_date))
    channel = _weighted_pick(rng, _month_channel_weights(rng, config, elapsed_months))
    region = _weighted_pick(rng, config.region_weights)
    city = rng.choice(REGION_CITIES.get(region, ["未知"]))
    device = _weighted_pick(rng, config.device_weights)

    # 买量渠道天然偏向低客单价套餐
    plan_weights = dict(config.plan_weights)
    if channel in ("付费广告", "社交媒体"):
        plan_weights["免费试用"] = plan_weights.get("免费试用", 0) + 14.0
        plan_weights["基础版"] = plan_weights.get("基础版", 0) + 8.0
        plan_weights["企业版"] = max(plan_weights.get("企业版", 0) - 6.0, 0.5)
    plan = _weighted_pick(rng, plan_weights)
    mrr = _mrr_for_plan(rng, plan)

    # 修正语义冲突：免费试用用户不可能是「试用转正/套餐升级」而来
    if plan == "免费试用" and activation_type in ("试用转正", "套餐升级"):
        activation_type = ACTIVATION_TYPES[0]

    # --- 流失模拟：离散时间 hazard 模型 ---
    churn_date: date | None = None
    cursor_month = month_start(signup_date)
    months_elapsed = 0
    while True:
        cursor_last = month_end(cursor_month)
        hazard = (
            config.base_monthly_churn_hazard
            * config.channel_churn_factor.get(channel, 1.0)
            * config.plan_churn_factor.get(plan, 1.0)
            * _tenure_churn_factor(months_elapsed)
        )
        hazard = min(hazard, 0.6)

        # 当月尚未走完时按已过天数折算，保证**日均**流失风险与整月一致。
        # 否则整月风险会被压缩进短短几天，趋势图末月出现虚假的流失尖峰。
        if cursor_month <= today < cursor_last:
            days_in_month = (cursor_last - cursor_month).days + 1
            days_elapsed = (today - cursor_month).days + 1
            hazard = 1 - (1 - hazard) ** (days_elapsed / days_in_month)

        if rng.random() < hazard:
            window_start = max(cursor_month, signup_date + timedelta(days=1))
            window_end = min(cursor_last, today)
            if window_start <= window_end:
                span = (window_end - window_start).days
                churn_date = window_start + timedelta(days=rng.randint(0, span))
                break

        if cursor_last >= today:
            break
        cursor_month = month_start(add_months(cursor_month, 1))
        months_elapsed += 1
        if months_elapsed > config.months + 2:
            break

    churn_reason = _pick_churn_reason(rng, plan, channel) if churn_date else ""
    end_reference = churn_date or today
    lifetime_days = max((end_reference - signup_date).days, 0)

    # --- 最后活跃日 ---
    # 这个字段必须生成，否则风险分会退化：控制器的 set_risk_score 在
    # last_active_on 为空时回退到「按开通日期算沉默天数」，
    # 结果所有老用户一律 100 分，字段和分析都失去意义。
    if churn_date:
        # 流失前通常先"变安静"一段时间
        last_active_on = max(churn_date - timedelta(days=rng.randint(0, 30)), signup_date)
    else:
        # 指数分布：多数人近期活跃，长尾延伸到数月 —— 这段长尾正是风险分的价值所在
        idle = min(int(rng.expovariate(1 / 21)), max((today - signup_date).days, 0))
        last_active_on = max(today - timedelta(days=idle), signup_date)

    # 流失说明是人工补录字段，只给一部分流失用户填写（真实系统里也常常是空的）
    churn_note = ""
    if churn_date and rng.random() < 0.45:
        churn_note = rng.choice(CHURN_NOTE_TEMPLATES).format(
            reason=churn_reason,
            days=lifetime_days,
            date=(churn_date.isoformat() if hasattr(churn_date, "isoformat") else churn_date),
        )


    record = {
        "name": f"USR-{index:05d}",
        "user_id": f"U{index:06d}",
        "user_name": f"用户{index:04d}",
        "email": f"user{index:04d}@example-growth.cn",
        "region": region,
        "city": city,
        "channel": channel,
        "device": device,
        "plan": plan,
        "mrr": mrr,
        "status": STATUS_CHURNED if churn_date else STATUS_ACTIVE,
        "activation_date": signup_date,
        "activation_type": activation_type,
        "churn_date": churn_date,
        "churn_reason": churn_reason,
        "churn_note": churn_note,
        "last_active_on": last_active_on,
        "lifetime_days": lifetime_days,
        "ltv": round(mrr * (lifetime_days / 30.4368), 2),
        "notes": "",
    }
    if config.include_status_history:
        record["status_history"] = _build_status_history(rng, record, mrr)
    return record


def generate_records(
    today: date | None = None,
    config: MockConfig | None = None,
    **overrides,
) -> list[dict]:
    """
    生成 Mock 数据集。

    :param today: 数据截止日，默认 ``date.today()``
    :param config: 参数集合，缺省使用 :class:`MockConfig`
    :param overrides: 临时覆盖 :class:`MockConfig` 的字段
    :return: 服务档案列表，按开通日期升序
    """
    if config is None:
        config = MockConfig(**overrides) if overrides else MockConfig()
    today = today or date.today()
    rng = random.Random(config.seed)

    current_month = month_start(today)
    records: list[dict] = []
    sequence = 0

    for offset in range(config.months - 1, -1, -1):
        month_first = month_start(add_months(current_month, -offset))
        elapsed = config.months - 1 - offset

        # 月度签约量 = 基线 × 复合增长 × 季节性 × 活动脉冲 × 噪声
        trend_base = config.base_monthly_signups * (
            (1 + config.monthly_growth_rate) ** elapsed
        )
        seasonal = config.seasonality.get(month_first.month, 1.0)
        pulse = config.campaign_pulses.get(offset, 1.0)
        noise = max(rng.normalvariate(1.0, 0.14), 0.55)
        signups = max(int(round(trend_base * seasonal * pulse * noise)), 1)

        for _ in range(signups):
            sequence += 1
            signup_date = _random_day_in_month(rng, month_first, today)
            if signup_date < month_first or signup_date > today:
                signup_date = min(max(signup_date, month_first), today)
            # 普通签约只在前 4 类来源里抽样，"流失回流"由专门的回流流程独占
            activation_type = rng.choices(
                NEW_USER_TYPES, weights=NEW_USER_TYPE_WEIGHTS, k=1
            )[0]
            records.append(
                _simulate_user(rng, config, sequence, signup_date, today, activation_type)
            )

    # 流失用户回流复购
    if config.reactivation_rate > 0:
        for source in [record for record in records if record["churn_date"]]:
            if rng.random() > config.reactivation_rate:
                continue
            reactivation_date = add_months(source["churn_date"], rng.randint(2, 7))
            if reactivation_date > today:
                continue
            sequence += 1
            new_plan = _weighted_pick(rng, config.plan_weights)
            record = _simulate_user(
                rng,
                config,
                sequence,
                reactivation_date,
                today,
                REACTIVATION_TYPE,
            )
            mrr = _mrr_for_plan(rng, new_plan)
            record.update(
                {
                    "user_id": source["user_id"],
                    "user_name": source["user_name"],
                    "email": source["email"],
                    "region": source["region"],
                    "city": source["city"],
                    "plan": new_plan,
                    "mrr": mrr,
                    "ltv": round(mrr * (record["lifetime_days"] / 30.4368), 2),
                    "notes": "流失后回流复购，同一客户主体",
                }
            )
            if record.get("status_history"):
                record["status_history"][0]["remark"] = "曾流失用户回流复购"
            records.append(record)

    records.sort(key=lambda item: (item["activation_date"], item["user_id"]))
    return records


def summarize(records: list[dict]) -> dict:
    """对生成结果做快速自检摘要，便于控制台核对数据质量。"""
    total = len(records)
    churned = [record for record in records if record["churn_date"]]
    active = [record for record in records if not record["churn_date"]]
    mrr = sum(record["mrr"] for record in active)
    months = sorted({month_key(record["activation_date"]) for record in records})
    return {
        "总记录数": total,
        "活跃用户数": len(active),
        "流失用户数": len(churned),
        "总流失率": f"{(len(churned) / total * 100 if total else 0):.1f}%",
        "当前 MRR": round(mrr, 2),
        "数据覆盖月份": f"{months[0]} ~ {months[-1]}" if months else "-",
        "平均生命周期(天)": round(
            sum(record["lifetime_days"] for record in records) / total, 1
        )
        if total
        else 0,
    }
