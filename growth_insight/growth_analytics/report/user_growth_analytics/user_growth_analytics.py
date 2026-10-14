"""
Growth Insight - 用户增长数据报表（Script Report）

为什么要做成「多视图」而不是 N 个报表
-------------------------------------
增长分析的读者其实是同一批人在问不同问题：这个月在涨吗？哪批人留下来了？哪个渠道买来的？
分成 7 个报表会让用户在报表列表里迷路，且每个报表要重复维护同一套过滤逻辑。
所以这里用一个 ``view`` 参数做路由，共用一条取数链路（``analytics/service.py``）。

为什么用 Script Report 而不是 Query Report
-----------------------------------------
* Cohort 留存矩阵的行列数随观察期动态变化，用纯 SQL 要把列名拼进字符串，
  还得自己处理转义，可读性差；
* 分层聚合（先取全量历史做趋势、再取当期 cohort 做下钻）用 ORM + Python 表达更清晰；
* analytics/core.py 与大屏共用同一份逻辑，杜绝"报表和大屏数字打架"。

返回 Frappe Script Report 的标准六元组：
``columns, data, message, chart, report_summary, skip_total_row``
"""

from frappe import _

from growth_insight.analytics import service
from growth_insight.analytics.constants import DIMENSIONS

#: 视图 -> (显示名, 是否合计行)
VIEWS = {
    "增长趋势": "按月看新增 / 流失 / 净增 / MRR 的完整时间序列",
    "Cohort 留存": "按开通月份分组的用户留存矩阵",
    "区域分布": "各区域的开通、活跃、流失与收入结构",
    "渠道质量": "渠道拉新能力 vs 留存质量的性价比对比",
    "套餐表现": "套餐构成、ARPU 与流失率的关系",
    "流失原因分析": "流失人群的归因分布",
    "明细数据": "逐条服务档案，用于导出与核对",
}

VIEW_ORDER = list(VIEWS.keys())


def execute(filters=None):
    """Frappe Script Report 入口。"""
    return GrowthReport(filters or {}).build()


class GrowthReport:
    """把报表组装逻辑封装成对象，避免 7 个视图平铺成一长串 if/else。"""

    def __init__(self, filters: dict):
        self.filters = filters
        self.view = filters.get("view") or VIEW_ORDER[0]
        if self.view not in VIEWS:
            self.view = VIEW_ORDER[0]
        self.scope = service.resolve_filters(filters)
        self.data = service.get_analysis(self.scope)

    # ------------------------------------------------------------------
    def build(self):
        handler = getattr(self, "_view_" + self._handler_name(), self._view_trend)
        columns, data, chart, summary, skip_total_row = handler()
        return (
            columns,
            data,
            self.render_message(),
            chart,
            summary,
            skip_total_row,
        )

    def _handler_name(self):
        mapping = {
            "增长趋势": "trend",
            "Cohort 留存": "retention",
            "区域分布": "dimension_region",
            "渠道质量": "dimension_channel",
            "套餐表现": "dimension_plan",
            "流失原因分析": "reasons",
            "明细数据": "detail",
        }
        return mapping.get(self.view, "trend")

    # ------------------------------------------------------------------
    # 公共片段
    # ------------------------------------------------------------------
    def render_message(self):
        """
        报表顶部的口径说明。

        刻意写明两件容易被误解的事：
        1. 趋势与维度下钻用的是不同口径的数据集（很多看板在这里做错）；
        2. 当月尚未走完，其新增/流失均为"部分月"数据，不能与完整月份横向比较。
        """
        scope = self.data["scope"]
        partial = next((row for row in self.data["trend"]["rows"] if row.get("is_partial")), None)

        lines = [
            '<div class="small text-muted" style="padding:2px 2px 10px;line-height:1.8;">',
            f"<div>{VIEWS[self.view]}</div>",
            "<div>统计区间：<b>{from_date} ~ {to_date}</b> ｜ 当期新增 cohort：<b>{count}</b> 条 ｜ "
            "趋势基线（含更早签约且仍在服的用户）：<b>{baseline}</b> 条</div>".format(
                from_date=scope["from_date"],
                to_date=scope["to_date"],
                count=scope["record_count"],
                baseline=scope["trend_baseline_count"],
            ),
        ]
        if partial:
            lines.append(
                '<div style="color:var(--orange,#f39c12);">'
                f"注意：<b>{partial['month']}</b> 尚未走完，新增/流失均为部分月数据，"
                "请勿与完整月份直接比较。</div>"
            )
        lines.append("</div>")
        return "".join(lines)

    def kpi_summary(self, extra: list | None = None) -> list:
        """顶部指标卡。"""
        kpi = self.data["kpi"]
        summary = [
            {
                "value": kpi["active_users"],
                "label": _("当前活跃用户"),
                "datatype": "Int",
                "indicator": "Green",
            },
            {
                "value": kpi["total_acquired"],
                "label": _("累计开通"),
                "datatype": "Int",
            },
            {
                "value": kpi["churned_users"],
                "label": _("累计流失"),
                "datatype": "Int",
                "indicator": "Red",
            },
            {
                "value": kpi["total_churn_rate"],
                "label": _("历史流失率 (%)"),
                "datatype": "Float",
                "precision": 1,
                "indicator": "Orange",
            },
            {
                "value": kpi["mrr"],
                "label": _("当前 MRR"),
                "datatype": "Currency",
                "currency": "CNY",
                "indicator": "Blue",
            },
        ]
        return (extra or []) + summary

    # ------------------------------------------------------------------
    # 视图 1：增长趋势
    # ------------------------------------------------------------------
    def _view_trend(self):
        rows = self.data["trend"]["rows"]
        columns = [
            {"label": _("月份"), "fieldname": "month", "fieldtype": "Data", "width": 100},
            {"label": _("期初活跃"), "fieldname": "opening_active", "fieldtype": "Int", "width": 90},
            {"label": _("新增"), "fieldname": "new", "fieldtype": "Int", "width": 80},
            {"label": _("流失"), "fieldname": "churned", "fieldtype": "Int", "width": 80},
            {"label": _("净增"), "fieldname": "net", "fieldtype": "Int", "width": 80},
            {"label": _("期末活跃"), "fieldname": "closing_active", "fieldtype": "Int", "width": 90},
            {
                "label": _("流失率"),
                "fieldname": "churn_rate",
                "fieldtype": "Percent",
                "width": 90,
            },
            {"label": _("新增 MRR"), "fieldname": "new_mrr", "fieldtype": "Currency", "width": 110},
            {
                "label": _("流失 MRR"),
                "fieldname": "churned_mrr",
                "fieldtype": "Currency",
                "width": 110,
            },
            {"label": _("净增 MRR"), "fieldname": "net_mrr", "fieldtype": "Currency", "width": 110},
            {
                "label": _("期末 MRR"),
                "fieldname": "closing_mrr",
                "fieldtype": "Currency",
                "width": 120,
            },
            {"label": _("净收入留存 NRR"), "fieldname": "nrr", "fieldtype": "Percent", "width": 120},
            {"label": _("ARPU"), "fieldname": "arpu", "fieldtype": "Currency", "width": 100},
        ]

        data = [dict(row) for row in rows]

        chart = {
            "data": {
                "labels": [row["month"] for row in rows],
                "datasets": [
                    {
                        "name": _("新增"),
                        "chartType": "bar",
                        "values": [row["new"] for row in rows],
                    },
                    {
                        "name": _("流失"),
                        "chartType": "bar",
                        "values": [row["churned"] for row in rows],
                    },
                    {
                        "name": _("期末活跃"),
                        "chartType": "line",
                        "values": [row["closing_active"] for row in rows],
                    },
                ],
            },
            "type": "axis-mixed",
            "fieldtype": "Int",
            "colors": ["#5e64ff", "#ff5858", "#28a745"],
            "lineOptions": {"regionFill": 1, "hideDots": 1},
            "barOptions": {"spaceRatio": 0.4},
            "axisOptions": {"xIsSeries": 1},
            "height": 300,
        }

        last = rows[-1] if rows else {}
        extra = [
            {
                "value": last.get("net", 0),
                "label": _("本月净增"),
                "datatype": "Int",
                "indicator": "Green" if last.get("net", 0) >= 0 else "Red",
            }
        ]
        return columns, data, chart, self.kpi_summary(extra), True

    # ------------------------------------------------------------------
    # 视图 2：Cohort 留存
    # ------------------------------------------------------------------
    def _view_retention(self):
        matrix = self.data["retention"]
        periods = matrix["periods"]
        columns = [
            {"label": _("开通月份"), "fieldname": "cohort", "fieldtype": "Data", "width": 100},
            {"label": _("群体规模"), "fieldname": "size", "fieldtype": "Int", "width": 90},
        ]
        for period in periods:
            columns.append(
                {
                    "label": _(period),
                    "fieldname": period,
                    "fieldtype": "Percent",
                    "width": 70,
                }
            )

        data = []
        for row in matrix["matrix"]:
            entry = dict(row)
            entry.pop("counts", None)
            entry.pop("values", None)
            # strict=False：留存矩阵的行尾可能不足 max_periods 列（Cohort 太新还没走完），
            # 此时缺列补空值即可，不应让整张报表报错
            for period, value in zip(periods, row["values"], strict=False):
                entry[period] = value
            data.append(entry)

        # 留存矩阵本身已经是二维表，不再叠加图表，避免信息重复
        return columns, data, None, self.kpi_summary(), True

    # ------------------------------------------------------------------
    # 视图 3~6：维度下钻（区域 / 渠道 / 套餐）
    # ------------------------------------------------------------------
    def _dimension_view(self, dimension: str):
        label = DIMENSIONS[dimension][0]
        rows = self.data[dimension]
        columns = [
            {"label": _(label), "fieldname": dimension, "fieldtype": "Data", "width": 120},
            {"label": _("累计开通"), "fieldname": "total", "fieldtype": "Int", "width": 90},
            {"label": _("当前活跃"), "fieldname": "active", "fieldtype": "Int", "width": 90},
            {"label": _("累计流失"), "fieldname": "churned", "fieldtype": "Int", "width": 90},
            {"label": _("占比"), "fieldname": "share", "fieldtype": "Percent", "width": 80},
            {"label": _("流失率"), "fieldname": "churn_rate", "fieldtype": "Percent", "width": 90},
            {
                "label": _("MRR"),
                "fieldname": "mrr",
                "fieldtype": "Currency",
                "width": 110,
            },
            {"label": _("ARPU"), "fieldname": "arpu", "fieldtype": "Currency", "width": 100},
            {
                "label": _("平均生命周期(月)"),
                "fieldname": "avg_lifetime_months",
                "fieldtype": "Float",
                "precision": 1,
                "width": 130,
            },
            {"label": _("LTV"), "fieldname": "ltv", "fieldtype": "Currency", "width": 110},
        ]
        data = [dict(row) for row in rows]

        chart = {
            "data": {
                "labels": [row[dimension] for row in rows],
                "datasets": [
                    {"name": _("累计开通"), "values": [row["total"] for row in rows]},
                    {"name": _("当前活跃"), "values": [row["active"] for row in rows]},
                ],
            },
            "type": "bar",
            "fieldtype": "Int",
            "colors": ["#5e64ff", "#28a745"],
            "barOptions": {"spaceRatio": 0.3, "stacked": 0},
            "height": 300,
        }
        return columns, data, chart, self.kpi_summary(), False

    def _view_dimension_region(self):
        return self._dimension_view("region")

    def _view_dimension_channel(self):
        return self._dimension_view("channel")

    def _view_dimension_plan(self):
        return self._dimension_view("plan")

    # ------------------------------------------------------------------
    # 视图 7：流失原因
    # ------------------------------------------------------------------
    def _view_reasons(self):
        rows = self.data["reasons"]
        columns = [
            {"label": _("流失原因"), "fieldname": "reason", "fieldtype": "Data", "width": 180},
            {"label": _("流失人数"), "fieldname": "count", "fieldtype": "Int", "width": 100},
            {"label": _("占比"), "fieldname": "share", "fieldtype": "Percent", "width": 90},
            {
                "label": _("平均生命周期(天)"),
                "fieldname": "avg_lifetime_days",
                "fieldtype": "Float",
                "precision": 1,
                "width": 140,
            },
            {
                "label": _("损失 MRR"),
                "fieldname": "lost_mrr",
                "fieldtype": "Currency",
                "width": 120,
            },
        ]
        data = [dict(row) for row in rows]
        chart = {
            "data": {
                "labels": [row["reason"] for row in rows],
                "datasets": [{"name": _("流失人数"), "values": [row["count"] for row in rows]}],
            },
            "type": "pie",
            "fieldtype": "Int",
            "height": 320,
        }
        return columns, data, chart, self.kpi_summary(), False

    # ------------------------------------------------------------------
    # 视图 8：明细
    # ------------------------------------------------------------------
    def _view_detail(self):
        # 明细视图直接走原始单据查询，便于对照过滤条件逐条下钻
        records = service.fetch_records(self.scope, apply_window=True)
        data = [
            {
                "name": row["name"],
                "user_id": row["user_id"],
                "user_name": row["user_name"],
                "region": row["region"],
                "channel": row["channel"],
                "plan": row["plan"],
                "mrr": row["mrr"],
                "activation_date": row["activation_date"],
                "activation_type": row["activation_type"],
                "churn_date": row["churn_date"],
                "status": row["status"],
                "lifetime_days": row["lifetime_days"],
                "churn_reason": row["churn_reason"],
            }
            for row in records
        ]
        columns = [
            {
                "label": _("单据"),
                "fieldname": "name",
                "fieldtype": "Link",
                "options": "User Service Record",
                "width": 160,
            },
            {"label": _("用户编号"), "fieldname": "user_id", "fieldtype": "Data", "width": 110},
            {"label": _("用户名称"), "fieldname": "user_name", "fieldtype": "Data", "width": 110},
            {"label": _("区域"), "fieldname": "region", "fieldtype": "Data", "width": 80},
            {"label": _("渠道"), "fieldname": "channel", "fieldtype": "Data", "width": 100},
            {"label": _("套餐"), "fieldname": "plan", "fieldtype": "Data", "width": 90},
            {"label": _("MRR"), "fieldname": "mrr", "fieldtype": "Currency", "width": 100},
            {
                "label": _("开通日期"),
                "fieldname": "activation_date",
                "fieldtype": "Date",
                "width": 110,
            },
            {"label": _("开通类型"), "fieldname": "activation_type", "fieldtype": "Data", "width": 100},
            {"label": _("流失日期"), "fieldname": "churn_date", "fieldtype": "Date", "width": 110},
            {"label": _("状态"), "fieldname": "status", "fieldtype": "Data", "width": 80},
            {"label": _("生命周期(天)"), "fieldname": "lifetime_days", "fieldtype": "Int", "width": 110},
            {"label": _("流失原因"), "fieldname": "churn_reason", "fieldtype": "Data", "width": 160},
        ]
        return columns, data, None, self.kpi_summary(), True
