# -*- coding: utf-8 -*-
# Copyright (c) 2026, Growth Insight Contributors and contributors
# For license information, please see license.txt

"""Service Status Change（子表行）——事件流水记录。

刻意保持"贫血"：它是**事实流水表**，不做任何业务推导。
所有计算都交给 ``analytics/core.py``，避免把业务规则散落在多
个层面导致口径分裂、以及升级时的隐式行为变更。
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class ServiceStatusChange(Document):
    def validate(self):
        # 子行缺日期会让上游的留存矩阵直接错行，这里是最后一道兜底
        if not self.event_date:
            frappe.throw(_("事件日期为必填项"))
        self.mrr = flt(self.mrr)
