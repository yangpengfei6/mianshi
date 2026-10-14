"""
安装 / 迁移钩子

三个入口的分工：

* ``before_install`` —— **必须**先创建自定义角色。
  DocType JSON 里直接声明了 ``Growth Analyst`` / ``Growth Viewer`` 权限，
  而 Frappe 安装流程是先跑 before_install 再同步 DocType，
  这样恰好在权限表落地前把角色准备好，避免 Link 校验失败。
* ``after_install``  —— 灌入 Mock 数据并做完整性自检。允许失败但不能中断安装，
  所以统一包了 try/except 并记录 Error Log。
* ``after_migrate``  —— 幂等地补齐角色与环境。App 从旧版本迁移上来时，
  after_install 不会再执行，缺的东西在这里补。
"""

import frappe
from frappe import _

#: 角色定义：(角色名, 是否开放 Desk 访问, 说明)
ROLES = [
    ("Growth Analyst", 1, "用户增长分析师：可读写服务档案、运行报表、查看大屏"),
    ("Growth Viewer", 1, "增长数据观察者：只读"),
]

APP_NAME = "growth_insight"

WELCOME_MESSAGE = """
<div style="line-height:1.9">
<p><strong>Growth Insight（用户增长分析套件）</strong> 安装完成 🎉</p>
<p>已自动为您准备好：</p>
<ul>
  <li>单据：<b>User Service Record</b>（含 Mock 数据）</li>
  <li>报表：<b>User Growth Analytics</b> —— 增长趋势 / Cohort 留存 / 渠道质量 / 流失归因 多视图</li>
  <li>大屏：<b>/app/growth-screen</b> —— 深色科技风实时看板</li>
</ul>
<p>提示：若需重建 Mock 数据，执行
<code>bench --site {site} execute growth_insight.mock.loader.regenerate_mock_data --kwargs "{{'force': 1}}"</code></p>
</div>
"""


def before_install():
    """在 DocType 同步之前把角色准备好。"""
    ensure_roles()


def after_install():
    ensure_roles()
    _install_mock_data_if_needed()
    _post_install_message()


def after_migrate():
    ensure_roles()


# ---------------------------------------------------------------------------
# 角色
# ---------------------------------------------------------------------------
def ensure_roles():
    """幂等创建/修复自定义角色。"""
    for role_name, desk_access, description in ROLES:
        if frappe.db.exists("Role", role_name):
            # 已存在时确保 Desk 访问标记正确（升级场景）
            current = frappe.db.get_value("Role", role_name, "desk_access")
            if current != desk_access:
                frappe.db.set_value("Role", role_name, "desk_access", desk_access)
            continue

        doc = frappe.get_doc(
            {
                "doctype": "Role",
                "role_name": role_name,
                "desk_access": desk_access,
                "description": description,
            }
        )
        doc.insert(ignore_permissions=True)
    frappe.db.commit()


# ---------------------------------------------------------------------------
# Mock 数据
# ---------------------------------------------------------------------------
def _install_mock_data_if_needed():
    # 生产环境可通过 site_config.json 关闭：{"growth_insight_skip_mock_data": 1}
    if frappe.conf.get("growth_insight_skip_mock_data"):
        frappe.flags.growth_insight_mock = "skipped by config"
        return

    if frappe.db.exists("DocType", "User Service Record") and frappe.db.count("User Service Record"):
        frappe.flags.growth_insight_mock = "already present"
        return

    try:
        from growth_insight.mock.loader import install_mock_data

        result = install_mock_data(force=False)
        frappe.flags.growth_insight_mock = result.get("message", "done")
    except Exception:
        # 数据没灌成功不应该让别人连 App 都装不上
        frappe.log_error(frappe.get_traceback(), _("Growth Insight: Mock 数据安装失败"))
        frappe.flags.growth_insight_mock = "failed (详见 Error Log)"


# ---------------------------------------------------------------------------
# 安装完成提示
# ---------------------------------------------------------------------------
def _post_install_message():
    site_name = frappe.local.site or "your.site"
    frappe.msgprint(WELCOME_MESSAGE.format(site=site_name), title=_("欢迎使用 Growth Insight"), wide=True)
