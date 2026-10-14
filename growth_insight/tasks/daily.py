"""
定时任务

每晚刷新「流失风险分」。

为什么用 set_value 而不是 doc.save()
------------------------------------
全表遍历场景下 ``frappe.get_doc(...).save()`` 会做：查 Document、跑一遍所有 hook、
更新 modified/modified_by、可能写共享表——对一个只读 Firebase 不改业务的派生指标来说太重。
这里先用轻量查询拿到字段，算完再 ``db.set_value`` 批量落库，且不刷新 modified
（避免把用户的"最近修改时间"刷新成今晚，干扰真实的数据录入时间线）。
"""

import frappe

from growth_insight.growth_analytics.doctype.user_service_record.user_service_record import (
    compute_risk_score,
)
from growth_insight.utils.helpers import chunked

DOCTYPE = "User Service Record"

RISK_FIELDS = [
    "name",
    "plan",
    "activation_date",
    "last_active_on",
    "churn_date",
]


def refresh_risk_scores(batch_size: int = 200) -> int:
    """批量重算流失风险分，返回更新条数。"""
    rows = frappe.db.get_all(
        DOCTYPE,
        filters={"docstatus": ("!=", 2)},
        fields=RISK_FIELDS,
        limit_page_length=0,
    )

    updated = 0
    for batch in chunked(rows, batch_size):
        for row in batch:
            score = compute_risk_score(row)
            previous = frappe.db.get_value(DOCTYPE, row["name"], "risk_score")
            # 只在变化时写库，进一步减少无谓 IO
            if previous is None or abs(float(previous) - score) > 0.05:
                frappe.db.set_value(
                    DOCTYPE, row["name"], "risk_score", score, update_modified=False
                )
                updated += 1
        frappe.db.commit()

    if updated:
        frappe.logger("growth_insight").info(
            f"[growth_insight] 流失风险分刷新完成，共更新 {updated} / {len(rows)} 条"
        )
    return updated
