/* eslint-disable */

/**
 * User Service Record 表单客户端脚本
 *
 * 原则：**客户端只做交互引导，不做数据把关**。
 * 所有真正的约束都在 Python 的 validate() 里，因为 JS 可以被 API / 数据导入绕过。
 */
frappe.ui.form.on("User Service Record", {
	refresh: function (frm) {
		frm.events.render_status_indicator(frm);
		frm.events.add_action_buttons(frm);
	},

	render_status_indicator: function (frm) {
		const colors = {
			活跃: "green",
			已流失: "red",
			已召回: "blue",
		};
		if (frm.doc.status) {
			frm.page.set_indicator(__(frm.doc.status), colors[frm.doc.status] || "gray");
		}
	},

	add_action_buttons: function (frm) {
		// 快捷操作：把"标记流失"这种高频动作做成一步到位，
		// 顺带强制要求填写原因——流失归因是整张大屏能否有用的前提。
		if (!frm.is_new() && frm.doc.status !== "已流失") {
			frm.add_custom_button(__("标记流失"), () => {
				frm.events.prompt_mark_churned(frm);
			});
		}

		if (frm.doc.status === "已流失") {
			frm.add_custom_button(__("恢复服务"), () => {
				frappe.confirm(
					__("确认恢复该用户的服务？将清空流失日期并把状态改为「已召回」。"),
					() => {
						frm.set_value("churn_date", null);
						frm.set_value("churn_reason", "");
						frm.set_value("status", "已召回");
						frm.save();
					}
				);
			});
		}

		if (!frm.is_new()) {
			frm.add_custom_button(__("查看同类流失分布"), () => {
				frappe.set_route("query-report", "User Growth Analytics", {
					view: "流失原因分析",
					plan: frm.doc.plan,
					channel: frm.doc.channel,
				});
			});
		}
	},

	prompt_mark_churned: function (frm) {
		frappe.prompt(
			[
				{
					label: __("流失日期"),
					fieldname: "churn_date",
					fieldtype: "Date",
					reqd: 1,
					default: frappe.datetime.get_today(),
				},
				{
					label: __("流失原因"),
					fieldname: "churn_reason",
					fieldtype: "Select",
					options: [
						"",
						"预算/价格因素",
						"功能不满足",
						"使用体验问题",
						"转向竞品",
						"服务支持不足",
						"业务调整/需求消失",
						"其他原因",
					].join("\n"),
					reqd: 1,
				},
				{
					label: __("补充说明"),
					fieldname: "churn_note",
					fieldtype: "Small Text",
				},
			],
			(values) => {
				frm.set_value("churn_date", values.churn_date);
				frm.set_value("churn_reason", values.churn_reason);
				frm.set_value("churn_note", values.churn_note);
				frm.save();
			},
			__("标记流失"),
			__("确认")
		);
	},

	// 套餐变更后自动带出该套餐的标准 MRR，避免手工填错金额影响收入类指标
	plan: function (frm) {
		if (!frm.doc.plan) return;
		frappe.call({
			method: "growth_insight.utils.helpers.get_plan_default_mrr",
			args: { plan: frm.doc.plan },
			freeze: false,
			callback: (response) => {
				const value = response.message;
				if (value === null || value === undefined) return;
				const current = frm.doc.mrr || 0;
				// 已有金额且明显高于套餐标准时不覆盖（可能是含折扣/加购的个例）
				if (current && current > value) return;
				frm.set_value("mrr", value);
			},
		});
	},

	status: function (frm) {
		frm.events.render_status_indicator(frm);
		frm.refresh();
	},

	// 表单层面给最后一次兜底提示，真正的拦截在 Python validate()
	validate: function (frm) {
		if (frm.doc.status === "已流失" && !frm.doc.churn_date) {
			frappe.msgprint({
				title: __("缺少必填信息"),
				indicator: "red",
				message: __("「已流失」状态必须填写流失日期"),
			});
			frappe.validated = false;
		}
	},
});
