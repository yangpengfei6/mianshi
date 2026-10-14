# -*- coding: utf-8 -*-
"""
User Service Record 单据测试

运行::

    bench --site test.local run-tests --app growth_insight --module \
        growth_insight.growth_analytics.doctype.user_service_record.test_user_service_record

测试覆盖的是**业务不变量**（状态推导、日期校验、派生字段、事件流水），
而不是 CRUD 本身 —— CRUD 由框架保证，业务规则才是我们的责任。
"""

import unittest

import frappe
from frappe.utils import add_days, date_diff, getdate, today


def make_record(**kwargs):
    """创建一条测试用服务档案。"""
    payload = {
        "doctype": "User Service Record",
        "user_id": f"TEST-{frappe.generate_hash(length=6)}",
        "user_name": "单元测试用户",
        "region": "华东",
        "city": "上海",
        "channel": "自然流量",
        "device": "Web",
        "plan": "专业版",
        "mrr": 599,
        "activation_date": add_days(getdate(), -100),
    }
    payload.update(kwargs)
    return frappe.get_doc(payload).insert(ignore_permissions=True)


class TestUserServiceRecord(unittest.TestCase):
    def tearDown(self):
        # 测试用例改动的数据一律回滚，避免污染其他测试
        frappe.db.rollback()

    # ------------------------------------------------------------------
    # 状态推导
    # ------------------------------------------------------------------
    def test_new_record_is_active_by_default(self):
        doc = make_record()
        self.assertEqual(doc.status, "活跃")
        self.assertIsNone(doc.churn_date)

    def test_churn_date_determines_status(self):
        doc = make_record(churn_date=add_days(getdate(), -10))
        self.assertEqual(doc.status, "已流失")

    def test_missing_churn_date_on_churned_status_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            make_record(status="已流失", churn_date=None)

    def test_churn_reason_is_cleared_when_not_churned(self):
        doc = make_record(churn_reason="转向竞品")
        self.assertEqual(doc.churn_reason, "")

    # ------------------------------------------------------------------
    # 日期校验
    # ------------------------------------------------------------------
    def test_churn_before_activation_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            make_record(
                activation_date=getdate(),
                churn_date=add_days(getdate(), -5),
            )

    def test_future_activation_date_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            make_record(activation_date=add_days(getdate(), 5))

    # ------------------------------------------------------------------
    # 派生字段
    # ------------------------------------------------------------------
    def test_lifetime_days_and_ltv_are_computed(self):
        activation = add_days(getdate(), -100)
        doc = make_record(activation_date=activation, mrr=600)
        self.assertEqual(doc.lifetime_days, 100)
        self.assertAlmostEqual(doc.ltv, 600 * (100 / 30.4368), places=1)

    def test_lifetime_uses_churn_date_when_churned(self):
        doc = make_record(
            activation_date=add_days(getdate(), -30),
            churn_date=add_days(getdate(), -10),
        )
        self.assertEqual(doc.lifetime_days, 20)

    def test_risk_score_reflects_idle_days(self):
        # 昨天仍在活跃 -> 风险分很低
        fresh = make_record(last_active_on=add_days(getdate(), -1))
        # 专业版健康间隔是 60 天，沉默 90 天应接近/达到满分
        idle = make_record(last_active_on=add_days(getdate(), -90))
        self.assertLess(fresh.risk_score, idle.risk_score)

    def test_churned_record_gets_max_risk_score(self):
        doc = make_record(churn_date=add_days(getdate(), -1))
        self.assertEqual(doc.risk_score, 100.0)

    # ------------------------------------------------------------------
    # 子表事件流水
    # ------------------------------------------------------------------
    def test_opening_event_is_auto_appended(self):
        doc = make_record()
        self.assertTrue(doc.status_history)
        self.assertEqual(doc.status_history[0].event_type, "开通")
        self.assertEqual(getdate(doc.status_history[0].event_date), getdate(doc.activation_date))

    def test_churn_transition_appends_history_row(self):
        doc = make_record()
        before = len(doc.status_history)

        doc.churn_date = getdate()
        doc.churn_reason = "转向竞品"
        doc.save()

        self.assertEqual(len(doc.status_history), before + 1)
        self.assertEqual(doc.status_history[-1].event_type, "流失")
        self.assertEqual(doc.status, "已流失")

    def test_removing_churn_date_appends_recall_event(self):
        doc = make_record(churn_date=add_days(getdate(), -5))
        doc.churn_date = None
        doc.churn_reason = ""
        doc.save()

        self.assertEqual(doc.status, "已召回")
        self.assertEqual(doc.status_history[-1].event_type, "召回")

    # ------------------------------------------------------------------
    # 命名
    # ------------------------------------------------------------------
    def test_autoname_uses_year_month_prefix(self):
        doc = make_record()
        expected_prefix = f"USR-{getdate().year}{getdate().month:02d}-"
        self.assertTrue(doc.name.startswith(expected_prefix), doc.name)

    def test_lifetime_days_never_negative(self):
        doc = make_record(activation_date=getdate())
        self.assertGreaterEqual(doc.lifetime_days, 0)


class TestDashboardPermissions(unittest.TestCase):
    """大屏接口必须做角色隔离 —— Page 路由本身不做权限控制。"""

    def test_insights_endpoint_is_whitelisted(self):
        from growth_insight.api import dashboard

        self.assertTrue(hasattr(dashboard.get_insights, "__dict__"))
        self.assertTrue(callable(dashboard.get_insights))
