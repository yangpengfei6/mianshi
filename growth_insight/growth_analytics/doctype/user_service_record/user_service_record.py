# -*- coding: utf-8 -*-
# Copyright (c) 2026, Growth Insight Contributors and contributors
# For license information, please see license.txt

"""
User Service Record 控制器

承载三类职责：
1. **命名**：这里用 ``autoname()`` 而不是 DocType 的 ``format:`` 规则 ——
   Frappe v16 已废弃旧式花括号占位符，写成方法可同时兼容 v14~v16；
2. **数据一致性**：日期/状态冲突、生命周期与 LTV 派生字段统一在 ``validate`` 兜住，
   而不是依赖前端填写正确（前端约束绕过的方式太多了：导入、API、脚本）；
3. **事件流水**：自动把关键状态跃迁追加到 ``status_history`` 子表，
   免去人工维护，同时为后续的分析留好明细数据。
"""

import frappe
from frappe.model.document import Document
from frappe.model.naming import getseries
from frappe.utils import cint, date_diff, flt, getdate, today

from growth_insight.analytics.constants import STATUS_ACTIVE, STATUS_CHURNED, STATUS_REACTIVATED

#: 各套餐的"健康活跃间隔"阈值（天），用于推算流失风险分
PLAN_ACTIVITY_INTERVAL = {
    "免费试用": 30,
    "基础版": 45,
    "专业版": 60,
    "旗舰版": 90,
    "企业版": 120,
}


class UserServiceRecord(Document):
    # ------------------------------------------------------------------
    # 命名
    # ------------------------------------------------------------------
    def autoname(self):
        """
        ``USR-202610-00001``

        前缀带年月，便于按月归档与肉眼识别；序列交由 ``tabSeries`` 原子递增，
        避免自己 select max()+1 带来的并发重复风险。
        """
        reference = getdate(self.get("activation_date") or today())
        prefix = f"USR-{reference.year}{reference.month:02d}-"
        self.name = prefix + getseries(prefix, 5)

    # ------------------------------------------------------------------
    # 校验与派生字段
    # ------------------------------------------------------------------
    def validate(self):
        self.sync_status_with_dates()
        self.validate_dates()
        self.set_lifetime_metrics()
        self.set_risk_score()
        self.sync_status_history()

    def before_submit(self):
        pass

    def sync_status_with_dates(self):
        """
        以「流失日期」为唯一事实来源推导状态。

        理由：状态是派生态，任何人从 Word 模板导入数据或调 API 都可能只填一半，
        若让状态与日期各说各话，下游所有留存/流失报表都会失去意义。
        """
        churn_date = getdate(self.churn_date) if self.churn_date else None
        if churn_date:
            self.status = STATUS_CHURNED
        elif self.status == STATUS_CHURNED:
            self.status = STATUS_ACTIVE
        if not self.status:
            self.status = STATUS_ACTIVE

    def validate_dates(self):
        activation = getdate(self.activation_date) if self.activation_date else None
        if not activation:
            return

        churn = getdate(self.churn_date) if self.churn_date else None
        if churn and churn < activation:
            frappe.throw("流失日期（{0}）不能早于服务开通日期（{1}）".format(churn, activation))

        # 未来日期几乎都是录入错误，会直接污染当月的 Cohort 留存分析
        if activation > getdate(today()):
            frappe.throw("服务开通日期不能晚于今天")
        if churn and churn > getdate(today()):
            frappe.throw("流失日期不能晚于今天")

        if not churn and self.churn_reason:
            # 允许填写但清理掉，避免"未流失却有原因"出现在流失归因报表里
            self.churn_reason = ""
        if self.status == STATUS_CHURNED and not self.churn_date:
            frappe.throw("状态为「已流失」时必须填写流失日期")

    def set_lifetime_metrics(self):
        """统一计算生命周期与 LTV，保证报表侧不需要重复 SQL。"""
        activation = getdate(self.activation_date) if self.activation_date else None
        if not activation:
            return

        churn = getdate(self.churn_date) if self.churn_date else None
        reference = churn or getdate(today())
        self.lifetime_days = cint(date_diff(reference, activation)) or 0
        self.ltv = flt(flt(self.mrr) * (self.lifetime_days / 30.4368), 2)

    def set_risk_score(self):
        """依据"距离最后活跃日的沉默天数 / 该套餐健康间隔"打分（0-100）。"""
        reference = getdate(self.last_active_on) if self.last_active_on else None
        if not reference:
            reference = getdate(self.activation_date) if self.activation_date else None
        if not reference:
            return

        idle_days = cint(date_diff(getdate(today()), reference)) or 0
        interval = PLAN_ACTIVITY_INTERVAL.get(self.plan, 60)
        # 已流失直接满分；沉默超过健康间隔即趋近 100
        if self.churn_date:
            score = 100.0
        else:
            score = min(100.0, idle_days / max(interval, 1) * 100)
        self.risk_score = round(score, 1)

    # ------------------------------------------------------------------
    # 事件流水自动同步
    # ------------------------------------------------------------------
    def sync_status_history(self):
        """
        把状态跃迁记入子表，避免业务同学漏填导致"有多少人流失后有召回动作"无法回答。

        mock 数据已在生成阶段写好了完整流水，因此这里只做**跃迁检测**增量追加，
        不会产生重复事件。
        """
        previous = self.get_doc_before_save()

        if not previous:
            self._append_missing_opening_event()
            return

        before_churn = getdate(previous.churn_date) if previous.churn_date else None
        after_churn = getdate(self.churn_date) if self.churn_date else None

        if not before_churn and after_churn:
            self.append(
                "status_history",
                {
                    "event_date": after_churn,
                    "event_type": "流失",
                    "plan": self.plan,
                    "mrr": 0,
                    "remark": self.churn_reason or "未填写原因",
                },
            )
            self.status = STATUS_CHURNED
        elif before_churn and not after_churn:
            # 流失被撤销：视为召回
            self.append(
                "status_history",
                {
                    "event_date": getdate(today()),
                    "event_type": "召回",
                    "plan": self.plan,
                    "mrr": self.mrr,
                    "remark": "流失状态被撤销，服务恢复",
                },
            )
            self.status = STATUS_REACTIVATED

    def _append_missing_opening_event(self):
        """新建单据且未手工填写流水时，自动补一条开通事件。"""
        if self.get("status_history"):
            return
        if not self.activation_date:
            return
        self.append(
            "status_history",
            {
                "event_date": getdate(self.activation_date),
                "event_type": "开通",
                "plan": self.plan,
                "mrr": self.mrr,
                "remark": "单据创建时自动补录",
            },
        )


def compute_risk_score(row: dict) -> float:
    """
    供定时任务批量计算风险分（**不加载 Document 对象**，直接面向 dict）。

    定时任务要遍历全表，若用 ``frappe.get_doc`` 逐条 save，
    800 条数据要十几秒且不必要地写 version/modified；这里先算再批量写库。
    """
    reference = getdate(row.get("last_active_on")) if row.get("last_active_on") else None
    if not reference:
        reference = getdate(row.get("activation_date")) if row.get("activation_date") else None
    if not reference:
        return 0.0
    if row.get("churn_date"):
        return 100.0
    idle_days = cint(date_diff(getdate(today()), reference)) or 0
    interval = PLAN_ACTIVITY_INTERVAL.get(row.get("plan"), 60)
    return round(min(100.0, idle_days / max(interval, 1) * 100), 1)
