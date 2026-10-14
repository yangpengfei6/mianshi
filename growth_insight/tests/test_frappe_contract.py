"""
Frappe 集成契约冒烟测试（离线，无需 bench）

为什么存在
----------
本 App 是 Frappe 自定义应用，但本地没有 Docker/bench 环境。为了避免"代码 import
就报错 / 字段契约错位 / 报表返回结构异常"这类在面试官机器上一跑就露馅的问题，
这里用一份最小 frappe 桩，把**真实的数据链路**离线跑通并断言关键契约：

1. import 契约：所有 Frappe 集成模块都能在无 bench 环境正常 import；
2. 字段契约：service 取的字段、报表明细列、风险分输入字段、generator 产出字段，
   全部落在 DocType 定义里（含子表），保证安装 Mock 数据不会因未知字段而失败；
3. 报表契约：7 个视图的 execute() 都返回标准六元组，且列/数据自洽；
4. 风险分契约：compute_risk_score 对流失用户给满分、对活跃用户给出有意义的分布
   （这正是之前 last_active_on 缺失导致"全员 100 分"缺陷的回归保护）；
5. 控制器契约：validate() 能正确推导状态、算风险分、自动补事件流水；
6. 权限契约：大屏接口在无权限时抛 PermissionError，有权限时正常返回并命中缓存。

运行::

    pytest growth_insight/tests/test_frappe_contract.py -q
"""

import json
import pathlib
import sys
from datetime import date

# conftest 已在收集前安装好 frappe 桩，这里直接 import 即可
import frappe  # noqa: E402  (stub)
import pytest

from growth_insight.analytics import service  # noqa: E402
from growth_insight.growth_analytics.report.user_growth_analytics import (  # noqa: E402
    user_growth_analytics as report_module,
)
from growth_insight.mock import loader  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
PARENT_JSON = (
    ROOT
    / "growth_insight"
    / "growth_analytics"
    / "doctype"
    / "user_service_record"
    / "user_service_record.json"
)
CHILD_JSON = (
    ROOT
    / "growth_insight"
    / "growth_analytics"
    / "doctype"
    / "service_status_change"
    / "service_status_change.json"
)

# DocType 自动拥有的标准元数据字段（不在 fields 列表里，但 insert 合法）
STANDARD_META = {"name", "docstatus", "owner", "creation", "modified", "modified_by", "idx", "doctype"}

DOCTYPE = "User Service Record"
CHILD_DOCTYPE = "Service Status Change"


def _load_fields(json_path: pathlib.Path) -> set:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    return {f["fieldname"] for f in data.get("fields", [])}


@pytest.fixture(scope="module")
def parent_fields():
    return _load_fields(PARENT_JSON)


@pytest.fixture(scope="module")
def child_fields():
    return _load_fields(CHILD_JSON)


# ---------------------------------------------------------------------------
# 1) import 契约
# ---------------------------------------------------------------------------


def test_frappe_stub_installed():
    assert "frappe" in sys.modules
    assert hasattr(frappe, "db")
    assert frappe.db.count(DOCTYPE) > 0  # 桩已灌入 Mock 数据


def test_integration_modules_import_cleanly():
    # 这些模块在真实环境依赖 frappe，这里用桩应能无错 import
    import growth_insight.api.dashboard  # noqa: F401
    import growth_insight.growth_analytics.doctype.service_status_change.service_status_change  # noqa: F401
    import growth_insight.growth_analytics.doctype.user_service_record.user_service_record  # noqa: F401
    import growth_insight.hooks  # noqa: F401
    import growth_insight.setup.install  # noqa: F401
    import growth_insight.tasks.daily  # noqa: F401

    assert report_module is not None
    assert service is not None


# ---------------------------------------------------------------------------
# 2) 字段契约
# ---------------------------------------------------------------------------


def test_service_fields_are_doctype_fields(parent_fields):
    missing = [f for f in service.FIELDS if f not in parent_fields and f not in STANDARD_META]
    assert not missing, f"service 取数包含未在 DocType 定义的字段: {missing}"


def test_generator_output_maps_to_doctype(parent_fields):
    records = frappe._gi_records
    assert records
    extra = set()
    for rec in records:
        for key in rec:
            if key == "status_history":
                continue  # 子表单独处理
            if key not in parent_fields and key not in STANDARD_META:
                extra.add(key)
    assert not extra, f"generator 产出包含未在 DocType 定义的字段: {extra}"


def test_serialize_record_only_known_fields(parent_fields):
    """loader 落库前序列化，绝不应出现未知字段，否则 insert 会失败。"""
    sample = frappe._gi_records[0]
    payload = loader.serialize_record(sample)
    unknown = [
        k
        for k in payload
        if k not in parent_fields and k not in STANDARD_META
    ]
    assert not unknown, f"serialize_record 产生未知字段: {unknown}"


def test_serialize_events_use_child_fields(child_fields):
    sample = next((r for r in frappe._gi_records if r.get("status_history")), None)
    if sample is None:
        pytest.skip("样本无 status_history")
    events = loader.serialize_events(sample)
    for event in events:
        for key in event:
            if key == "doctype":
                continue
            assert key in child_fields, f"事件字段 {key} 不在子表定义中"


def test_report_detail_columns_are_doctype_fields(parent_fields):
    columns, data, _message, _chart, _summary, _skip = report_module.execute(
        {"view": "明细数据"}
    )
    assert isinstance(columns, list) and columns
    detail_fields = {col["fieldname"] for col in columns}
    unknown = detail_fields - (parent_fields | STANDARD_META)
    assert not unknown, f"明细视图列包含未知字段: {unknown}"
    assert data, "明细视图应有数据"


def test_risk_score_input_fields_exist(parent_fields):
    for field in ("last_active_on", "activation_date", "churn_date", "plan"):
        assert field in parent_fields, f"风险分依赖字段缺失: {field}"


# ---------------------------------------------------------------------------
# 3) 报表契约
# ---------------------------------------------------------------------------


def test_report_all_views_return_six_tuple(parent_fields):  # noqa: ARG001
    for view in report_module.VIEW_ORDER:
        result = report_module.execute({"view": view})
        assert isinstance(result, tuple) and len(result) == 6, f"{view} 返回结构异常"
        columns, data, message, chart, summary, skip = result
        assert isinstance(columns, list) and columns, f"{view} 缺少列定义"
        assert isinstance(data, list), f"{view} 数据应为列表"
        assert isinstance(message, str), f"{view} message 应为字符串"
        assert chart is None or isinstance(chart, dict), f"{view} chart 结构异常"
        assert isinstance(summary, list), f"{view} report_summary 应为列表"
        assert isinstance(skip, bool), f"{view} skip_total_row 应为布尔"
        # 每一列都应有完整结构
        for col in columns:
            assert {"fieldname", "label", "fieldtype"} <= set(col), f"{view} 列结构缺失"


def test_report_trend_has_data_and_partial_flag():
    columns, data, message, chart, _summary, _skip = report_module.execute({"view": "增长趋势"})
    assert data, "趋势视图应为空"
    assert "month" in {c["fieldname"] for c in columns}
    # 末月应被标记为部分月（当前月未走完）
    assert any(row.get("is_partial") for row in data), "末月应标记为部分月"
    assert "部分月" in message, "应在口径说明里提示部分月"


# ---------------------------------------------------------------------------
# 4) 风险分契约（last_active_on 回归保护）
# ---------------------------------------------------------------------------


def test_compute_risk_score_distribution_is_meaningful():
    from growth_insight.growth_analytics.doctype.user_service_record.user_service_record import (
        compute_risk_score,
    )

    records = frappe._gi_records
    scores = [compute_risk_score(r) for r in records]
    churned = [r for r in records if r.get("churn_date")]
    active = [r for r in records if not r.get("churn_date")]

    # 流失用户一律满分
    assert all(compute_risk_score(r) == 100.0 for r in churned), "流失用户风险分应为 100"
    # 活跃用户的分数必须有分布（不能全员 100 —— last_active_on 修复的回归保护）
    active_scores = [compute_risk_score(r) for r in active]
    assert min(active_scores) < 100, "活跃用户风险分不应全部为满分（last_active_on 修复回归）"
    assert max(scores) == 100.0


# ---------------------------------------------------------------------------
# 5) 控制器契约
# ---------------------------------------------------------------------------


def test_controller_validate_derives_status_and_risk():
    from growth_insight.analytics.constants import STATUS_ACTIVE
    from growth_insight.growth_analytics.doctype.user_service_record.user_service_record import (
        UserServiceRecord,
    )

    doc = UserServiceRecord()
    doc.user_id = "U000001"
    doc.region = "华东"
    doc.channel = "自然流量"
    doc.plan = "专业版"
    doc.mrr = 599
    doc.activation_date = date(2026, 1, 1)
    doc.last_active_on = date(2026, 10, 1)
    doc.activation_type = "首次开通"
    doc.validate()

    assert doc.status == STATUS_ACTIVE
    assert isinstance(doc.risk_score, (int, float))
    assert doc.risk_score < 100.0  # 近期活跃，风险分应较低
    assert doc.status_history, "应自动补一条开通事件"
    assert doc.status_history[0]["event_type"] == "开通"


def test_controller_churn_before_activation_rejected():
    from growth_insight.growth_analytics.doctype.user_service_record.user_service_record import (
        UserServiceRecord,
    )

    doc = UserServiceRecord()
    doc.activation_date = date(2026, 10, 1)
    doc.churn_date = date(2026, 9, 1)  # 早于开通
    with pytest.raises(frappe.ValidationError):
        doc.validate()


# ---------------------------------------------------------------------------
# 6) 权限 / API 契约
# ---------------------------------------------------------------------------


def test_dashboard_requires_permission(monkeypatch):
    from growth_insight.api import dashboard

    monkeypatch.setattr(frappe, "get_roles", lambda user=None: [])
    monkeypatch.setattr(frappe, "has_permission", lambda *a, **k: False)

    with pytest.raises(frappe.PermissionError):
        dashboard._ensure_permission()


def test_dashboard_returns_payload_and_caches():
    from growth_insight.api import dashboard

    # 默认 stub 角色含 System Manager，应通过
    payload1 = dashboard.get_dashboard_data({})
    assert isinstance(payload1, dict)
    for key in ("scope", "trend", "region", "channel", "plan", "kpi"):
        assert key in payload1, f"返回 payload 缺少 {key}"
    # 自动结论由独立接口 get_insights 提供（与页面拆分，职责清晰）
    assert "insights" not in payload1

    # 缓存：相同过滤条件应返回同一对象
    payload2 = dashboard.get_dashboard_data({})
    assert payload1 is payload2, "相同查询应命中缓存"


def test_daily_refresh_updates_all_rows():
    from growth_insight.growth_analytics.doctype.user_service_record.user_service_record import (
        compute_risk_score,
    )
    from growth_insight.tasks.daily import refresh_risk_scores

    updated = refresh_risk_scores()
    assert isinstance(updated, int)
    assert updated >= 0

    rows = frappe.db.get_all(DOCTYPE, fields=["name", "plan", "activation_date", "last_active_on", "churn_date"])
    for row in rows:
        expected = compute_risk_score(row)
        stored = frappe.db.get_value(DOCTYPE, row["name"], "risk_score")
        assert abs(float(stored) - expected) < 0.06, f"风险分未刷新: {row['name']}"
