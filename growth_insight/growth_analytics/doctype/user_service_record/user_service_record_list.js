/* eslint-disable */

/**
 * User Service Record 列表视图增强
 *
 * 列表页是业务同学最高频的入口，因此在这里做两件事：
 * 1. 用色块让"活跃/已流失"一眼可辨（Frappe 原生 indicator）；
 * 2. 预置高频过滤条件（近 30 天流失、本月新增、付费用户），
 *    省去每次手工搭 filter 的成本。
 */
frappe.listview_settings["User Service Record"] = {
	add_fields: ["mrr", "lifetime_days", "plan", "churn_date"],

	get_indicator(doc) {
		// 色值沿用 Frappe 语义：green=健康, red=风险, gray=终止, blue=信息
		if (doc.status === "已流失") {
			return [__("已流失"), "red", "status,=,已流失"];
		}
		if (doc.status === "已召回") {
			return [__("已召回"), "blue", "status,=,已召回"];
		}
		if (doc.mrr === 0) {
			return [__("试用中"), "orange", "plan,=,免费试用"];
		}
		return [__("活跃中"), "green", "status,=,活跃"];
	},

	onload(listview) {
		// 预置视图：覆盖最常见的 4 类自助分析场景
		const presets = [
			{
				label: __("本月新增"),
				filters: [
					["User Service Record", "activation_date", "Timespan", "this month"],
				],
			},
			{
				label: __("近 30 天流失"),
				filters: [
					["User Service Record", "status", "=", "已流失"],
					["User Service Record", "churn_date", "Timespan", "last month"],
				],
			},
			{
				label: __("付费用户"),
				filters: [["User Service Record", "mrr", ">", 0]],
			},
			{
				label: __("待跟进（流失未填原因）"),
				filters: [
					["User Service Record", "status", "=", "已流失"],
					["User Service Record", "churn_reason", "is", "not set"],
				],
			},
		];

		(listview.page.sidebar || listview.$filter_section).addClass("growth-listview");

		presets.forEach((preset) => {
			listview.page.add_sidebar_item(preset.label, () => {
				listview.filter_area.clear(false);
				listview.filter_area.add(preset.filters).then(() => {
					listview.refresh();
				});
			}, 1);
		});
	},
};
