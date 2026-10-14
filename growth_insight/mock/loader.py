"""
Mock 数据落库器

职责：把 :func:`growth_insight.mock.generator.generate_records` 产出的
纯 dict 写进数据库。**不含任何业务规则**，便于单独替换数据源。

设计取舍
--------
* 走标准的 ``Document.insert()`` 而非 ``db_insert()``：
  慢一些（约 800 条 / 数十秒），但会真正跑一遍控制器的 ``validate()``，
  相当于每次安装都做一次"单据层集成冒烟测试"。
* 每 100 条 commit 一次：安装过程中若失败，已写入部分不回滚，
  便于定位到第几条数据出问题，而不是整批无痕丢失。
* 幂等：默认已有数据就跳过；``force=True`` 才会清空重建。
"""

import frappe
from frappe.utils import getdate

from growth_insight.mock.generator import MockConfig, generate_records

DOCTYPE = "User Service Record"

#: 子表字段名
CHILD_TABLE = "status_history"


def serialize_record(record: dict) -> dict:
    """把生成器输出的 date 对象转成 Frappe 能直接落库的形态。"""
    payload = {"doctype": DOCTYPE}
    for fieldname, value in record.items():
        if fieldname == CHILD_TABLE:
            continue
        if value is None:
            continue
        payload[fieldname] = value.isoformat() if hasattr(value, "isoformat") else value
    return payload


def serialize_events(record: dict) -> list[dict]:
    rows = []
    for event in record.get(CHILD_TABLE) or []:
        rows.append(
            {
                "doctype": "Service Status Change",
                "event_date": event["event_date"].isoformat()
                if hasattr(event["event_date"], "isoformat")
                else event["event_date"],
                "event_type": event.get("event_type"),
                "plan": event.get("plan"),
                "mrr": event.get("mrr"),
                "remark": event.get("remark"),
            }
        )
    return rows


def install_mock_data(force: bool = False, **overrides) -> dict:
    """
    安装 Mock 数据。

    :param force: True 时先清空现有数据再重建
    :return: 统计信息 dict
    """
    existing = frappe.db.count(DOCTYPE)
    if existing and not force:
        return {
            "status": "skipped",
            "message": f"已存在 {existing} 条记录，跳过 Mock 数据安装（force=True 可重建）",
            "created": 0,
        }

    if existing and force:
        # 先删子表，避免孤儿行
        frappe.db.delete("Service Status Change", {"parenttype": DOCTYPE})
        frappe.db.delete(DOCTYPE, {})
        frappe.db.commit()

    config = MockConfig(**overrides) if overrides else MockConfig()
    today = getdate()
    records = generate_records(today=today, config=config)

    created = 0
    for index, record in enumerate(records, start=1):
        doc = frappe.get_doc(serialize_record(record))
        for event in serialize_events(record):
            doc.append(CHILD_TABLE, event)

        doc.flags.ignore_links = True
        doc.insert(ignore_permissions=True, ignore_mandatory=True)
        created += 1

        if index % 100 == 0:
            frappe.db.commit()
            # 安装后不会立即用到，这里主动回收一次，避免长事务堆积
            frappe.clear_messages()

    frappe.db.commit()
    return {
        "status": "created",
        "message": f"已生成 {created} 条用户服务档案",
        "created": created,
    }


@frappe.whitelist()
def regenerate_mock_data(force: int = 0, **overrides) -> dict:
    """
    供控制台手动触发的命令::

        bench --site xxx execute growth_insight.mock.loader.regenerate_mock_data --kwargs "{'force': 1}"
    """
    if not frappe.has_permission(DOCTYPE, "write") and "System Manager" not in frappe.get_roles():
        frappe.throw("仅具备写权限的用户可重建 Mock 数据")
    return install_mock_data(force=bool(force), **overrides)
