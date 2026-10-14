"""Growth Insight 大屏数据 API。

安全设计：
* Frappe 的 Desk Page 本身不做角色隔离（任何登录用户都能敲路由），
  所以**权限必须在接口层卡**，这里统一做角色校验；
* 大屏会被放在会议室常亮，每隔几十秒自动刷新一次，
  因此结果做短 TTL 缓存，避免多人同时看导致数据库被报表查询压垮。
"""

import hashlib
import json

import frappe
from frappe import _

from growth_insight.analytics import core, service
from growth_insight.analytics.constants import DIMENSIONS

#: 允许访问增长数据的角色
ALLOWED_ROLES = {"System Manager", "Growth Analyst", "Growth Viewer"}

#: 缓存秒数。小于页面刷新间隔即可，保证"大屏看起来是实时的"但不打爆数据库
CACHE_TTL = 60


def _ensure_permission():
    """统一的权限校验入口。"""
    roles = set(frappe.get_roles()) or set()
    if roles & ALLOWED_ROLES:
        return
    # 兜底：对该单据有读权限的用户也放行（例如客户自己扩展的自定义角色）
    if frappe.has_permission("User Service Record", ptype="read"):
        return
    frappe.throw(_("您没有查看用户增长数据的权限，请联系管理员申请 Growth Viewer 角色"), frappe.PermissionError)


def json_safe(value):
    """递归把 date / Decimal 等不可 JSON 序列化的对象转为原生类型。"""
    from datetime import date, datetime
    from decimal import Decimal

    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _cache_key(scope: dict, suffix: str = "dashboard") -> str:
    fingerprint = json.dumps(scope, sort_keys=True, default=str)
    digest = hashlib.md5(fingerprint.encode("utf-8")).hexdigest()[:16]
    return f"growth_insight:{suffix}:{digest}"


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
@frappe.whitelist()
def get_dashboard_data(filters=None, use_cache=1):
    """
    大屏主数据源。

    :param filters: dict 或 JSON 字符串
    :param use_cache: 传 0 可绕过缓存（顶部"强制刷新"按钮使用）
    """
    _ensure_permission()

    if isinstance(filters, str):
        filters = frappe.parse_json(filters) or {}
    scope = service.resolve_filters(filters or {})
    scope.pop("as_of", None)  # 不作为缓存 key 的一部分，避免同一查询反复穿透

    cache_key = _cache_key(scope)
    cache = frappe.cache()
    if int(use_cache):
        cached = cache.get_value(cache_key)
        if cached:
            return cached

    payload = json_safe(service.get_analysis(service.resolve_filters(filters or {})))
    payload["generated_at"] = frappe.utils.now()
    payload["server_logged_in_user"] = frappe.session.user

    if int(use_cache):
        cache.set_value(cache_key, payload, expires_in_sec=CACHE_TTL)
    return payload


@frappe.whitelist()
def get_filter_options():
    """返回过滤器可选值，避免前端硬编码字典（字典以 constants.py 为准）。"""
    _ensure_permission()

    options = {
        "dimension_label": {key: label for key, (label, _values) in DIMENSIONS.items()},
        "static": {
            key: values for key, values in DIMENSIONS.items() if values[1]
        },
    }
    # 城市数量多且随数据变化，动态取
    options["static"]["city"] = []
    for key in ("region", "channel", "plan", "device"):
        options["static"][key] = DIMENSIONS[key][1] or []
    return options


@frappe.whitelist()
def get_insights():
    """
    自动结论：把数据里最值得注意的若干条洞察算出来，直接呈现在大屏上。

    这是让大屏"能被用起来"的关键——纯图表让人看到一堆柱子，
    但管理层真正想看的是"所以呢"。规则本身在 ``analytics/core.py``，
    这里只负责取数 + 国际化包装。
    """
    _ensure_permission()

    scope = service.resolve_filters({})
    records = service.fetch_records(scope, apply_window=True)
    insights = core.derive_insights(records, scope["as_of"])
    for item in insights:
        item["text"] = _(item["text"])
    return insights
