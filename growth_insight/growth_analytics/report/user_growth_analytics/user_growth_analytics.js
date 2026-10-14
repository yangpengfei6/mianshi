/* eslint-disable */

/**
 * User Growth Analytics —— 报表过滤器与交互增强
 *
 * 用 Select 做视图路由（而不是拆成 7 个报表）：
 * 读者是同一批人，只是一次问一个问题，让他们在一个页面里切换视角，
 * 比在报表列表里找名字要高效得多。
 */

const GROWTH_OPTIONS = {
	region: ["华东", "华南", "华北", "华中", "西南", "西北", "东北", "海外"],
	channel: [
		"自然流量",
		"搜索引擎",
		"内容营销",
		"社交媒体",
		"转介绍",
		"线下活动",
		"应用商店",
		"付费广告",
	],
	plan: ["免费试用", "基础版", "专业版", "旗舰版", "企业版"],
	device: ["Web", "移动端 App", "小程序", "桌面客户端", "开放 API"],
	status: ["活跃", "已流失", "已召回"],
};

frappe.query_reports["User Growth Analytics"] = {
	filters: [
		{
			fieldname: "view",
			label: __("分析视角"),
			fieldtype: "Select",
			options: [
				"增长趋势",
				"Cohort 留存",
				"区域分布",
				"渠道质量",
				"套餐表现",
				"流失原因分析",
				"明细数据",
			].join("\n"),
			default: "增长趋势",
			reqd: 1,
			on_change: () => frappe.query_report.refresh(),
		},
		{
			fieldname: "from_date",
			label: __("统计起始月份"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -24),
		},
		{
			fieldname: "to_date",
			label: __("统计截止日期"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "region",
			label: __("区域"),
			fieldtype: "MultiSelectList",
			get_data: () => GROWTH_OPTIONS.region,
		},
		{
			fieldname: "channel",
			label: __("获客渠道"),
			fieldtype: "MultiSelectList",
			get_data: () => GROWTH_OPTIONS.channel,
		},
		{
			fieldname: "plan",
			label: __("订阅套餐"),
			fieldtype: "MultiSelectList",
			get_data: () => GROWTH_OPTIONS.plan,
		},
		{
			fieldname: "exclude_free",
			label: __("排除免费试用"),
			fieldtype: "Check",
			description: __("勾选后只统计产生 MRR 的付费用户，用于观察真实收入结构"),
		},
	],

	/**
	 * 给关键列上色：一眼看出"哪个月净增为负""哪个渠道流失率偏高"。
	 * 注意 formatter 会被每一格调用，逻辑必须保持轻量（不做远程请求）。
	 */
	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		const fieldname = column.fieldname;

		if (fieldname === "net" || fieldname === "net_mrr") {
			if (Number(data[fieldname]) < 0) return `<span style="color:var(--red,#e03636);font-weight:600;">${value}</span>`;
			if (Number(data[fieldname]) > 0) return `<span style="color:var(--green,#28a745);">${value}</span>`;
		}

		if (fieldname === "churn_rate" && Number(data[fieldname]) >= 30) {
			return `<span style="color:var(--red,#e03636);font-weight:600;">${value}</span>`;
		}

		if (fieldname === "status") {
			const colors = { 活跃: "green", 已流失: "red", 已召回: "blue" };
			const color = colors[data[fieldname]] || "gray";
			return `<span class="indicator-pill ${color}">${value}</span>`;
		}

		return value;
	},

	onload: function (report) {
		report.page.add_inner_button(__("打开增长大屏"), () => {
			window.open("/app/growth-screen", "_blank");
		});
	},
};
