# -*- coding: utf-8 -*-
"""
生成大屏渲染测试所需的数据夹具。

复用 **真实的** generator + core 产出数据，而不是手写几行假 JSON ——
这样前端验证跑的是和实际页面一样的计算链路。

运行::

    python scripts/make_fixture.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from growth_insight.analytics import core  # noqa: E402
from growth_insight.mock.generator import generate_records  # noqa: E402

TODAY = date(2026, 10, 14)
OUTPUT = ROOT / "_dash_fixture.json"


def clean(value):
    """把 date 对象转成字符串，便于 JSON 序列化。"""
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return value


def main() -> int:
    records = core.normalize_records(generate_records(today=TODAY))
    months = core.month_sequence(core.add_months(TODAY, -23), TODAY)
    active = [row for row in records if not row["churn_date"]]

    payload = {
        "trend": core.growth_trend(records, months=months, as_of=TODAY),
        "retention": core.retention_matrix(records, TODAY, max_periods=12),
        "region": core.group_breakdown(records, "region", TODAY),
        "channel": core.group_breakdown(records, "channel", TODAY),
        "plan": core.group_breakdown(active, "plan", TODAY),
        "reasons": core.churn_reason_breakdown(records),
        "tenure": core.tenure_distribution(records, TODAY),
        "funnel": core.value_funnel(records, TODAY),
        "kpi": core.snapshot(records, TODAY),
        "insights": core.derive_insights(records, TODAY),
        "events": core.recent_events(records, limit=30, as_of=TODAY),
        "scope": {
            "from_date": months[0] + "-01",
            "to_date": TODAY.isoformat(),
            "record_count": len(records),
            "trend_baseline_count": len(records),
        },
    }

    OUTPUT.write_text(
        json.dumps(clean(payload), ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"已写入 {OUTPUT.relative_to(ROOT)}: {len(records)} 条记录, {len(months)} 个月份")
    return 0


if __name__ == "__main__":
    sys.exit(main())
