"""Growth Insight - Frappe App Hooks

设计要点：所有"非数据损伤性"的副作用都有幂等/可跳过/可重试的入口，
保证任何一个环节失败都不会让整个 App 装不上。
"""

from . import __version__ as app_version

app_name = "growth_insight"
app_title = "Growth Insight"
app_publisher = "Growth Insight Contributors"
app_description = "用户服务开通/流失单据 + 用户增长报表 + 增长数据大屏"
app_icon = "octicon octicon-graph"
app_color = "blue"
app_email = "growth-insight@example.com"
app_license = "MIT"
app_publisher_url = "https://github.com/yangpengfei6/mianshi"
source_link = "https://github.com/yangpengfei6/mianshi"

required_apps = ["frappe"]

# ---------------------------------------------------------------------------
# 安装生命周期
# ---------------------------------------------------------------------------
# before_install：必须先建好自定义角色，因为 DocType JSON 的 permissions 里
# 直接声明了 Growth Analyst / Growth Viewer，而 Frappe 是在**之后**才同步 DocType。
before_install = "growth_insight.setup.install.before_install"
after_install = "growth_insight.setup.install.after_install"
# 老版本升级上来时 after_install 不会重跑，角色/缺件在这里补齐
after_migrate = "growth_insight.setup.install.after_migrate"

# ---------------------------------------------------------------------------
# 定时任务
# ---------------------------------------------------------------------------
scheduler_events = {
    "daily": ["growth_insight.tasks.daily.refresh_risk_scores"],
}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
# 说明：刻意**没有**把 Workspace 做成 fixture。
# Workspace 的 content 是 JSON-in-JSON，且各版本之间卡片/区块结构仍在演进，
# 固化成 JSON 一旦与目标版本不匹配就会让安装流程报错。
# 因此导航入口采用"模块路由 + 报表内跳转按钮"的方式，详见 docs/DESIGN.md 第 6 节。
fixtures = []

# ---------------------------------------------------------------------------
# 权限钩子
# ---------------------------------------------------------------------------
# 大屏 Page 自身不做角色隔离，权限统一在 api/dashboard.py 的接口层拦截
has_permission = {}

# ---------------------------------------------------------------------------
# 版本信息
# ---------------------------------------------------------------------------
__version__ = app_version
