/**
 * 生成大屏静态预览页 docs/preview.html
 *
 * 关键点：**不重复实现任何一份渲染代码**。
 * 这里直接从 growth_screen.js 里抽取纯渲染部分（on_page_load 之前），
 * 用同一组函数、同一套 CSS、同一组布局常量拼出 HTML，
 * 因此预览页与实际页面在数据变化时必然同步，不会出现"预览好看、线上错位"。
 *
 * 运行： node scripts/make_preview.js
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const PAGE_JS = path.join(
	ROOT,
	"growth_insight",
	"growth_analytics",
	"page",
	"growth_screen",
	"growth_screen.js"
);
const FIXTURE = path.join(ROOT, "_dash_fixture.json");
const OUTPUT = path.join(ROOT, "docs", "preview.html");

if (!fs.existsSync(FIXTURE)) {
	console.error("缺少数据夹具，请先运行: python scripts/make_fixture.py");
	process.exit(1);
}

// --- 在沙箱里加载页面上方的纯函数区 ---
const source = fs.readFileSync(PAGE_JS, "utf8");
const MARKER = 'frappe.pages["growth-screen"].on_page_load';
const cutIndex = source.indexOf(MARKER);
if (cutIndex < 0) throw new Error("未找到 on_page_load 标记");

const sandbox = { frappe: { provide: () => {} }, __: (s) => s, console };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
// 注意：vm 里用 const/let 声明的顶层常量不会自动成为 context 的属性
// （只有 var / function 声明会），所以这里显式追加一段导出再执行。
vm.runInContext(
	source.slice(0, cutIndex) +
		`
;globalThis.__GI__ = {
	giNum, giEsc, giInt, giMoney, giPct, giNiceCeil, giSvg,
	giTrendChart, giBarList, giDonut, giColumnChart, giFunnel,
	GI_COLORS, GI_PALETTE, GI_LAYOUT, GI_COL, GI_PANEL_W, GI_CSS,
};`,
	sandbox
);

const {
	giNum,
	giEsc,
	giInt,
	giMoney,
	giPct,
	giTrendChart,
	giBarList,
	giDonut,
	giColumnChart,
	giFunnel,
	GI_CSS,
	GI_COLORS,
	GI_PALETTE,
	GI_LAYOUT,
	GI_PANEL_W,
} = sandbox.__GI__;

const data = JSON.parse(fs.readFileSync(FIXTURE, "utf8"));
const { canvasW, canvasH, pad, gap, headerH, kpiH, rowBH, rowCH } = GI_LAYOUT;

/* ---------------- 各区块 ---------------- */
function renderHead() {
	const kpi = data.kpi || {};
	return `
	<div class="gi-head" style="height:${headerH}px;">
		<div>
			<div class="gi-title">GROWTH INSIGHT · 用户增长数据大屏</div>
			<div class="gi-sub">统计区间 ${giEsc((data.scope || {}).from_date)} ~ ${giEsc(
		(data.scope || {}).to_date
	)} ｜ 当前 Cohort ${giInt((data.scope || {}).record_count)} 条 ｜ 趋势基线 ${giInt(
		(data.scope || {}).trend_baseline_count
	)} 条</div>
		</div>
		<div class="gi-clock">
			<div class="t">10:24:36</div>
			<div class="d">2026年10月14日 · 星期三</div>
		</div>
	</div>`;
}

function renderKpis() {
	const kpi = data.kpi || {};
	const cards = [
		["累计开通", giInt(kpi.total_acquired), GI_COLORS.indigo, `近 30 天 +${giInt(kpi.new_last_period)}`],
		["当前活跃用户", giInt(kpi.active_users), GI_COLORS.accent, `占开通 ${giPct(
			(giNum(kpi.active_users) / Math.max(giNum(kpi.total_acquired), 1)) * 100
		)}`],
		["累计流失", giInt(kpi.churned_users), GI_COLORS.red, `历史流失率 ${giPct(kpi.total_churn_rate)}`],
		["近 30 天净增", giInt(kpi.net_last_period), giNum(kpi.net_last_period) >= 0 ? GI_COLORS.green : GI_COLORS.red, `新增 ${giInt(kpi.new_last_period)} / 流失 ${giInt(kpi.churned_last_period)}`],
		["月度经常性收入 MRR", giMoney(kpi.mrr), GI_COLORS.amber, `ARR ${giMoney(kpi.arr)}`],
		["ARPU / 平均生命周期", giMoney(kpi.arpu), GI_COLORS.purple, `ARPU ${giMoney(kpi.arpu)} · ${giNum(kpi.avg_lifetime_months).toFixed(1)} 月`],
	];
	return (
		`<div class="gi-row" style="height:${kpiH}px;margin-bottom:${gap}px;">` +
		cards
			.map(
				([label, value, color, delta], index) => `
			<div class="gi-panel gi-kpi" style="flex:1;${
				index < cards.length - 1 ? `margin-right:${gap}px;` : ""
			}">
				<span class="ico" style="background:${color}22;color:${color};">◈</span>
				<div>
					<div class="num" style="color:${color};">${value}</div>
					<div class="lab">${label}</div>
					<div class="delta" style="color:${GI_COLORS.textDim};">${delta}</div>
				</div>
			</div>`
			)
			.join("") +
		`</div>`
	);
}

function panel(span, title, innerGetter) {
	return `<div class="gi-panel" style="flex:0 0 ${GI_PANEL_W(span)}px;">
		<div class="gi-panel-title">${giEsc(title)}</div>
		<div class="gi-panel-body">${innerGetter(GI_PANEL_W(span) - 32)}</div>
	</div>`;
}

function renderRowB() {
	return (
		`<div class="gi-row" style="height:${rowBH}px;margin-bottom:${gap}px;">` +
		panel(3, "区域分布（Top 8）", (w) =>
			giBarList((data.region || []).slice(0, 8), (r) => r.region, (r) => r.active, {
				suffixFn: (r) => ` / ${giInt(r.total)}`,
			})
		) +
		`<div class="gi-panel" style="flex:0 0 ${GI_PANEL_W(6)}px;">
			<div class="gi-panel-title">月度增长趋势</div>
			<div class="gi-panel-body">${giTrendChart(
				(data.trend || { rows: [] }).rows,
				GI_PANEL_W(6) - 32,
				GI_LAYOUT.rowBH - 84
			)}</div>
		</div>` +
		panel(3, "渠道质量 vs 留存", (w) =>
			giBarList((data.channel || []).slice(0, 8), (r) => r.channel, (r) => r.total, {
				colorFn: (r) =>
					giNum(r.churn_rate) >= 35
						? GI_COLORS.red
						: giNum(r.churn_rate) >= 22
						? GI_COLORS.amber
						: GI_COLORS.green,
				suffixFn: (r) => `流失 ${giPct(r.churn_rate)}`,
			})
		) +
		`</div>`
	);
}

function renderRowC() {
	const insights = (data.insights || []).map(
		(item) =>
			`<div class="gi-insight ${
				item.type === "warning" ? "warn" : item.type === "success" ? "ok" : ""
			}"><span>${item.type === "warning" ? "⚠" : "◈"}</span><span>${giEsc(item.text)}</span></div>`
	);
	return (
		`<div class="gi-row" style="height:${rowCH}px;">` +
		panel(3, "套餐构成（当前在服）", (w) =>
			giDonut(data.plan || [], "plan", "active", Math.min(w, 190), 190)
		) +
		panel(3, "生命周期分布", (w) =>
			giColumnChart(
				data.tenure || [],
				"bucket",
				[
					{ key: "active", label: "在服", color: GI_COLORS.green },
					{ key: "churned", label: "已流失", color: GI_COLORS.red },
				],
				w,
				GI_LAYOUT.rowCH - 96
			)
		) +
		panel(3, "转化漏斗 & 自动结论", (w) =>
			giFunnel(data.funnel || [], w, 150) +
			`<div style="margin-top:8px;font-size:13px;font-weight:700;">自动结论（后端计算）</div>` +
			`<div class="gi-insights">${insights.join("") || '<div class="gi-insight">—</div>'}</div>`
		) +
		panel(3, "实时事件流", (w) => renderEvents()) +
		`</div>`
	);
}

function renderEvents() {
	const events = data.events || [];
	if (!events.length) return '<div class="gi-empty">暂无事件</div>';
	const list = events
		.map(
			(e) => `
		<div class="gi-event ${e.type === "开通" ? "new" : "lost"}">
			<span style="color:${e.type === "开通" ? GI_COLORS.green : GI_COLORS.red};">●</span>
			<span style="color:${GI_COLORS.textDim};">${giEsc(String(e.date).slice(5))}</span>
			<span>${giEsc(e.user_name)}</span>
			<span style="color:${GI_COLORS.textDim};">${giEsc(e.plan)}·${giEsc(e.region)}</span>
			<span class="rp">${e.type === "开通" ? "+" : "-"}${giEsc(e.type)}</span>
		</div>`
		)
		.join("");
	return `<div class="gi-events"><div>${list}</div></div>`;
}

const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>Growth Insight 大屏预览</title>
<style>
html,body{margin:0;padding:0;background:#04070f;}
*::-webkit-scrollbar{width:0;height:0;}
${GI_CSS}
</style>
</head>
<body>
<div class="gi-root">
	<div class="gi-canvas" style="width:${canvasW}px;height:${canvasH}px;padding:${pad}px;">
		${renderHead()}
		${renderKpis()}
		${renderRowB()}
		${renderRowC()}
		<div class="gi-foot" style="height:${GI_LAYOUT.footerH}px;margin-top:${gap}px;">
			<span>口径：趋势基于全量历史计算期初活跃；下钻基于统计区间内新增 cohort</span>
			<span>静态预览 · 数据与渲染函数均取自 growth_screen.js</span>
		</div>
	</div>
</div>
<script>
	// 与线上页面一致的等比缩放逻辑
	function fit(){
		var root=document.querySelector('.gi-root'),canvas=document.querySelector('.gi-canvas');
		var s=Math.min((root.clientWidth-4)/${canvasW},(root.clientHeight-4)/${canvasH});
		var w=${canvasW}*s, off=Math.max((root.clientWidth-4-w)/2,0);
		canvas.style.transform='translateX('+off+'px) scale('+s+')';
	}
	fit();window.addEventListener('resize',fit);
</script>
</body>
</html>
`;

fs.writeFileSync(OUTPUT, html, "utf8");
console.log(`已生成 ${path.relative(ROOT, OUTPUT)} (${html.length} bytes)`);
