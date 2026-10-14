/* eslint-disable */

/**
 * Growth Insight —— 用户增长数据大屏
 *
 * ── 为什么不用 ECharts / Chart.js？ ────────────────────────────────────
 * 1. **零外部依赖**：大屏常部署在内网或离线环境，CDN 拉不到就白屏；
 *    手写的 SVG 不依赖任何第三方库，`bench build` 之外不需要任何构建步骤。
 * 2. **体积与可控性**：我们要的图形（折线/柱/环/漏斗）都很基础，
 *    引入一个 300KB 的图表库换来的是自己完全无法调整的渲染细节。
 * 3. **大屏幕特殊性**：1920×1080 固定画布 + 等比缩放，
 *    比"自适应容器"更能保证投屏时版式不崩。
 *
 * ── 关键设计决策 ──────────────────────────────────────────────────────
 * * 固定 1920×1080 画布，`transform: scale()` 等比贴合窗口 ——投屏不会错位；
 * * 数据与报表**同源**（都走 analytics/core.py），不重复实现 rate 算法；
 * * 页面隐藏（切 Tab）时暂停轮询，回来立即补一次 ——会议室大屏常年开着，
 *   不暂停会白白压数据库；
 * * 自动结论（insights）由后端算，前端只负责呈现。
 */

frappe.provide("growth_insight.screen");

/* =========================================================================
 * 1. 主题常量
 * ========================================================================= */
const GI_COLORS = {
	bg0: "#050a18",
	bg1: "#0a1330",
	panel: "rgba(14, 30, 64, 0.72)",
	border: "rgba(64, 158, 255, 0.28)",
	text: "#e8f1ff",
	textDim: "#8ea8cc",
	accent: "#22d3ee",
	indigo: "#7c9cff",
	green: "#34d399",
	red: "#f87171",
	amber: "#fbbf24",
	purple: "#a78bfa",
};

const GI_PALETTE = [
	"#22d3ee",
	"#7c9cff",
	"#34d399",
	"#fbbf24",
	"#a78bfa",
	"#f472b6",
	"#60a5fa",
	"#f87171",
	"#2dd4bf",
];

/* =========================================================================
 * 2. 布局常量（固定画布，等比缩放）
 * ========================================================================= */
const GI_LAYOUT = {
	canvasW: 1920,
	canvasH: 1080,
	pad: 22,
	gap: 16,
	headerH: 72,
	kpiH: 116,
	rowBH: 384,
	rowCH: 372,
	footerH: 32,
};

const GI_COL =
	(GI_LAYOUT.canvasW - GI_LAYOUT.pad * 2 - GI_LAYOUT.gap * 11) / 12;

const GI_PANEL_W = (span) => GI_COL * span + GI_LAYOUT.gap * (span - 1);

/* =========================================================================
 * 3. 样式
 * ========================================================================= */
const GI_CSS = `
.gi-root{padding:0 !important;margin:0 !important;position:relative;width:100%;height:calc(100vh - 60px);overflow:hidden;background:
  radial-gradient(1200px 700px at 20% -10%, rgba(34,211,238,.10), transparent 60%),
  radial-gradient(1000px 600px at 90% 110%, rgba(124,156,255,.10), transparent 60%),
  linear-gradient(160deg, ${GI_COLORS.bg0} 0%, ${GI_COLORS.bg1} 60%, #071026 100%);
  color:${GI_COLORS.text};font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif;}
.gi-canvas{position:absolute;top:0;left:0;transform-origin:top left;display:flex;flex-direction:column;}
.gi-head{display:flex;align-items:center;justify-content:space-between;padding:0 6px;}
.gi-title{font-size:32px;font-weight:800;letter-spacing:2px;
  background:linear-gradient(90deg,#67e8f9,#a5b4fc);-webkit-background-clip:text;background-clip:text;color:transparent;}
.gi-sub{font-size:13px;color:${GI_COLORS.textDim};margin-top:2px;}
.gi-clock{text-align:right;}
.gi-clock .t{font-size:30px;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:1px;}
.gi-clock .d{font-size:12px;color:${GI_COLORS.textDim};}
.gi-row{display:flex;}
.gi-panel{background:${GI_COLORS.panel};border:1px solid ${GI_COLORS.border};border-radius:10px;
  backdrop-filter:blur(2px);display:flex;flex-direction:column;overflow:hidden;
  box-shadow:0 0 0 1px rgba(255,255,255,.02) inset, 0 8px 30px rgba(0,0,0,.25);}
.gi-panel-title{font-size:15px;font-weight:700;padding:12px 16px 4px;display:flex;align-items:center;gap:8px;}
.gi-panel-title::before{content:"";width:4px;height:14px;border-radius:2px;background:${GI_COLORS.accent};}
.gi-panel-body{flex:1;padding:6px 16px 14px;min-height:0;}
.gi-kpi{display:flex;align-items:center;gap:14px;padding:0 18px;}
.gi-kpi .ico{width:46px;height:46px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:22px;}
.gi-kpi .num{font-size:34px;font-weight:800;font-variant-numeric:tabular-nums;line-height:1.05;}
.gi-kpi .lab{font-size:13px;color:${GI_COLORS.textDim};margin-top:2px;}
.gi-kpi .delta{font-size:12px;margin-top:3px;}
.gi-tag{font-size:11px;padding:2px 7px;border-radius:20px;border:1px solid;opacity:.95;}
.gi-foot{font-size:11px;color:#61789b;padding:0 8px;display:flex;align-items:center;justify-content:space-between;}
.gi-bars-row{display:flex;align-items:center;gap:10px;margin:7px 0;}
.gi-bars-row .nm{font-size:12.5px;width:86px;flex:0 0 86px;color:${GI_COLORS.text};}
.gi-bars-row .track{flex:1;height:10px;border-radius:6px;background:rgba(255,255,255,.07);overflow:hidden;}
.gi-bars-row .fill{height:100%;border-radius:6px;}
.gi-bars-row .val{width:104px;flex:0 0 104px;text-align:right;font-size:12.5px;font-variant-numeric:tabular-nums;color:${GI_COLORS.text};}
.gi-insights{display:flex;flex-direction:column;gap:8px;}
.gi-insight{display:flex;gap:9px;align-items:flex-start;font-size:12.5px;line-height:1.65;
  background:rgba(255,255,255,.04);border-left:3px solid ${GI_COLORS.accent};border-radius:6px;padding:8px 10px;color:#cfe0f7;}
.gi-insight.warn{border-left-color:${GI_COLORS.red};}
.gi-insight.ok{border-left-color:${GI_COLORS.green};}
.gi-events{overflow:hidden;position:relative;height:100%;}
.gi-events-track{animation:gi-scroll 42s linear infinite;}
@keyframes gi-scroll{from{transform:translateY(0)}to{transform:translateY(-50%)}}
.gi-event{display:flex;align-items:center;gap:10px;padding:9px 10px;margin-bottom:7px;border-radius:7px;
  background:rgba(255,255,255,.045);border-left:2px solid ${GI_COLORS.textDim};font-size:12.5px;}
.gi-event.new{border-left-color:${GI_COLORS.green};}
.gi-event.lost{border-left-color:${GI_COLORS.red};}
.gi-event .rp{margin-left:auto;font-variant-numeric:tabular-nums;color:${GI_COLORS.textDim};font-size:12px;}
.gi-legend{display:flex;gap:16px;justify-content:center;font-size:12px;color:${GI_COLORS.textDim};padding:2px 0 6px;}
.gi-legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;}
.gi-empty{display:flex;align-items:center;justify-content:center;height:100%;color:${GI_COLORS.textDim};font-size:14px;}
.gi-loading{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(5,10,24,.6);z-index:9;font-size:15px;}
`;

/* =========================================================================
 * 4. 工具函数
 * ========================================================================= */
const giNum = (v) => (typeof v === "number" && !isNaN(v) ? v : parseFloat(v || 0) || 0);

function giEsc(value) {
	return String(value === null || value === undefined ? "" : value).replace(
		/[&<>"']/g,
		(ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]
	);
}

function giInt(v) {
	return Math.round(giNum(v)).toLocaleString("zh-CN");
}

function giMoney(v) {
	const n = giNum(v);
	const abs = Math.abs(n);
	if (abs >= 10000) return "¥" + (n / 10000).toFixed(1) + "万";
	return "¥" + Math.round(n).toLocaleString("zh-CN");
}

function giPct(v) {
	return giNum(v).toFixed(1) + "%";
}

/** 取一个"好看"的上界，避免 Y 轴出现 637 这种刻度 */
function giNiceCeil(value) {
	if (value <= 0) return 1;
	const exp = Math.floor(Math.log10(value));
	const base = Math.pow(10, exp);
	const scaled = value / base;
	const step = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((s) => scaled <= s) || 10;
	return step * base;
}

function giSvg(w, h, inner) {
	return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg">${inner}</svg>`;
}

/* =========================================================================
 * 5. 图表渲染器（全部返回 SVG / HTML 字符串）
 * ========================================================================= */

/**
 * 月度增长趋势：柱（新增/流失）+ 折线（期末活跃）。
 * 柱和线共用一条 Y 轴 —— 刻意如此：两根柱对比看当月进出，线看存量水位。
 */
function giTrendChart(rows, w, h) {
	if (!rows || !rows.length) return '<div class="gi-empty">暂无数据</div>';
	const pad = { l: 52, r: 14, t: 14, b: 30 };
	const iw = w - pad.l - pad.r;
	const ih = h - pad.t - pad.b;
	const maxVal = giNiceCeil(Math.max(...rows.map((r) => r.closing_active), 1));
	const slot = iw / rows.length;
	const bw = Math.max(3, Math.min(13, slot * 0.3));

	const yOf = (v) => pad.t + ih - (giNum(v) / maxVal) * ih;
	let parts = "";

	// 网格 + Y 轴刻度
	for (let i = 0; i <= 4; i++) {
		const val = (maxVal / 4) * i;
		const y = yOf(val);
		parts += `<line x1="${pad.l}" y1="${y}" x2="${w - pad.r}" y2="${y}" stroke="rgba(255,255,255,.08)" stroke-width="1"/>`;
		parts += `<text x="${pad.l - 8}" y="${y + 4}" text-anchor="end" font-size="11" fill="${GI_COLORS.textDim}">${Math.round(val)}</text>`;
	}

	// 期末活跃：面积 + 折线
	const linePts = rows.map(
		(r, i) => `${pad.l + slot * (i + 0.5)},${yOf(r.closing_active)}`
	);
	parts += `<polygon points="${pad.l},${pad.t + ih} ${linePts.join(" ")} ${pad.l + iw},${
		pad.t + ih
	}" fill="url(#giGrad)" opacity="0.35"/>`;
	parts += `<polyline points="${linePts.join(" ")}" fill="none" stroke="${GI_COLORS.accent}" stroke-width="2.2" stroke-linejoin="round"/>`;

	// 新增 / 流失柱（最后一个月若未走完，用虚线圈出来提醒）
	rows.forEach((r, i) => {
		const cx = pad.l + slot * (i + 0.5);
		const newH = Math.max(0, pad.t + ih - yOf(r.new));
		const lostH = Math.max(0, pad.t + ih - yOf(r.churned));
		const dashed = r.is_partial ? ' stroke-dasharray="2 2" stroke="rgba(255,255,255,.5)"' : "";
		parts += `<rect x="${cx - bw - 1}" y="${pad.t + ih - newH}" width="${bw}" height="${newH}" rx="2" fill="${GI_COLORS.green}" opacity=".85"${dashed}/>`;
		parts += `<rect x="${cx + 1}" y="${pad.t + ih - lostH}" width="${bw}" height="${lostH}" rx="2" fill="${GI_COLORS.red}" opacity=".85"${dashed}/>`;
	});

	// X 轴标签：超过 14 个月就隔一个显示，避免糊成一团
	const stride = rows.length > 14 ? (rows.length > 20 ? 3 : 2) : 1;
	rows.forEach((r, i) => {
		if (i % stride !== 0 && i !== rows.length - 1) return;
		const cx = pad.l + slot * (i + 0.5);
		const label = String(r.month).replace(/^\d{4}-/, "");
		parts += `<text x="${cx}" y="${h - 10}" text-anchor="middle" font-size="10.5" fill="${GI_COLORS.textDim}">${giEsc(
			label
		)}月</text>`;
	});

	const grad = `<defs><linearGradient id="giGrad" x1="0" y1="0" x2="0" y2="1">
		<stop offset="0%" stop-color="${GI_COLORS.accent}" stop-opacity=".65"/>
		<stop offset="100%" stop-color="${GI_COLORS.accent}" stop-opacity="0"/></linearGradient></defs>`;

	return (
		`<div class="gi-legend"><span><i style="background:${GI_COLORS.green}"></i>新增</span>` +
		`<span><i style="background:${GI_COLORS.red}"></i>流失</span>` +
		`<span><i style="background:${GI_COLORS.accent}"></i>期末活跃</span></div>` +
		giSvg(w, h, grad + parts)
	);
}

/** 水平条形榜，带数值与辅助信息 */
function giBarList(rows, keyFn, valFn, opts = {}) {
	if (!rows || !rows.length) return '<div class="gi-empty">暂无数据</div>';
	const max = giNiceCeil(Math.max(...rows.map(valFn), 1));
	const colorFn = opts.colorFn || (() => GI_COLORS.indigo);
	return rows
		.map((row, i) => {
			const pct = (giNum(valFn(row)) / max) * 100;
			const suffix = opts.suffixFn ? `<span class="rp">${giEsc(opts.suffixFn(row))}</span>` : "";
			return `<div class="gi-bars-row">
				<span class="nm" title="${giEsc(keyFn(row))}">${giEsc(keyFn(row))}</span>
				<span class="track"><span class="fill" style="width:${pct.toFixed(
					1
				)}%;background:linear-gradient(90deg, ${
					typeof colorFn === "function" ? colorFn(row, i) : GI_COLORS.indigo
				} 0%, ${(typeof colorFn === "function" ? colorFn(row, i) : GI_COLORS.indigo)}55 100%);"></span></span>
				<span class="val">${giInt(valFn(row))}${suffix}</span>
			</div>`;
		})
		.join("");
}

/** 甜甜圈图 + 图例 */
function giDonut(rows, keyField, valueField, w, h) {
	if (!rows || !rows.length) return '<div class="gi-empty">暂无数据</div>';
	const total = rows.reduce((sum, r) => sum + giNum(r[valueField]), 0) || 1;
	const size = Math.min(w, h);
	const cx = size / 2;
	const cy = size / 2;
	const r = size / 2 - 14;
	const thickness = 26;
	let offset = 0;
	let arcs = "";

	rows.forEach((row, i) => {
		const value = giNum(row[valueField]);
		const fraction = value / total;
		const circumference = 2 * Math.PI * r;
		const dash = fraction * circumference;
		const color = GI_PALETTE[i % GI_PALETTE.length];
		arcs += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${color}" stroke-width="${thickness}"
			stroke-dasharray="${dash} ${circumference - dash}" stroke-dashoffset="${-offset}"
			transform="rotate(-90 ${cx} ${cy})" opacity=".92"/>`;
		offset += dash;
	});

	const top = rows[0];
	const svg =
		giSvg(size, size, arcs) +
		`<div style="margin-top:4px;font-size:12px;color:${GI_COLORS.textDim};">
			<span style="color:${GI_PALETTE[0]}">■</span> 占比最高：${giEsc(top[keyField])} ${giPct(
				(giNum(top[valueField]) / total) * 100
			)}
		</div>`;

	const legend = rows
		.map(
			(row, i) =>
				`<div style="display:flex;align-items:center;gap:8px;font-size:12px;padding:3px 0;">
					<i style="width:9px;height:9px;border-radius:2px;background:${
						GI_PALETTE[i % GI_PALETTE.length]
					};display:inline-block;"></i>
					<span style="flex:1;color:#cfe0f7;">${giEsc(row[keyField])}</span>
					<span style="font-variant-numeric:tabular-nums;">${giInt(row[valueField])}</span>
					<span style="width:52px;text-align:right;color:${GI_COLORS.textDim};">${giPct(
						(giNum(row[valueField]) / total) * 100
					)}</span>
				</div>`
		)
		.join("");

	return `<div style="display:flex;gap:14px;align-items:center;height:100%;">
		<div style="flex:0 0 ${size + 8}px;display:flex;flex-direction:column;align-items:center;">${svg}</div>
		<div style="flex:1;min-width:0;">${legend}</div>
	</div>`;
}

/** 纵向柱状图（生命周期分布） */
function giColumnChart(rows, labelKey, pairs, w, h) {
	if (!rows || !rows.length) return '<div class="gi-empty">暂无数据</div>';
	const pad = { l: 34, r: 10, t: 12, b: 34 };
	const iw = w - pad.l - pad.r;
	const ih = h - pad.t - pad.b;
	const maxVal = giNiceCeil(
		Math.max(...rows.map((r) => pairs.reduce((s, p) => s + giNum(r[p.key]), 0)), 1)
	);
	const slot = iw / rows.length;
	const bw = Math.max(8, Math.min(30, slot * 0.52));
	let parts = "";

	for (let i = 0; i <= 3; i++) {
		const y = pad.t + ih - (ih / 3) * i;
		parts += `<line x1="${pad.l}" y1="${y}" x2="${w - pad.r}" y2="${y}" stroke="rgba(255,255,255,.07)"/>`;
		parts += `<text x="${pad.l - 6}" y="${y + 4}" text-anchor="end" font-size="10" fill="${
			GI_COLORS.textDim
		}">${Math.round((maxVal / 3) * i)}</text>`;
	}

	rows.forEach((row, i) => {
		const cx = pad.l + slot * (i + 0.5);
		let stackBottom = 0;
		pairs.forEach((pair) => {
			const value = giNum(row[pair.key]);
			const barH = (value / maxVal) * ih;
			const y = pad.t + ih - stackBottom - barH;
			parts += `<rect x="${cx - bw / 2}" y="${y}" width="${bw}" height="${Math.max(
				barH,
				0
			)}" fill="${pair.color}" opacity=".9"/>`;
			stackBottom += barH;
		});
		parts += `<text x="${cx}" y="${h - 18}" text-anchor="middle" font-size="10.5" fill="${
			GI_COLORS.textDim
		}">${giEsc(row[labelKey])}</text>`;
	});

	const legend = `<div class="gi-legend">${pairs
		.map((p) => `<span><i style="background:${p.color}"></i>${giEsc(p.label)}</span>`)
		.join("")}</div>`;
	return legend + giSvg(w, h, parts);
}

/** 漏斗：横向条 + 转化率标注 */
function giFunnel(rows, w, h) {
	if (!rows || !rows.length) return '<div class="gi-empty">暂无数据</div>';
	const baseline = giNum(rows[0].count) || 1;
	const rowH = Math.min(60, Math.floor((h - 10) / rows.length));
	let parts = "";
	rows.forEach((row, i) => {
		const ratio = giNum(row.count) / baseline;
		const y = i * rowH;
		const bw = (w - 120) * Math.max(ratio, 0.06);
		const color = GI_PALETTE[i % GI_PALETTE.length];
		parts += `<g><polygon points="70,${y + 4} ${70 + bw},${y + 4} ${70 + bw - 10},${
			y + rowH - 8
		} 70,${y + rowH - 8}" fill="${color}" opacity=".85"/>
		<text x="8" y="${y + rowH / 2 + 2}" font-size="12" fill="${GI_COLORS.text}">${giEsc(
			row.stage
		)}</text>
		<text x="${w - 8}" y="${
			y + rowH / 2 + 2
		}" text-anchor="end" font-size="12.5" fill="${GI_COLORS.text}">${giInt(
			row.count
		)} <tspan fill="${GI_COLORS.textDim}" font-size="11">${giPct(row.rate)}</tspan></text></g>`;
	});
	return giSvg(w, h, parts);
}

/* =========================================================================
 * 6. 页面主体
 * ========================================================================= */
frappe.pages["growth-screen"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("用户增长数据大屏"),
		single_column: true,
	});

	const state = {
		timer: null,
		countdown: 0,
		tick: null,
		scale: 1,
		loading: false,
		lastRendered: null,
	};

	setupStyles();
	$(page.body).addClass("gi-root");
	wrapper.classList.add("gi-host");

	// 顶部工具栏
	page.clear_menu();
	const rangeField = page.add_field({
		label: __("统计区间"),
		fieldtype: "Select",
		fieldname: "range",
		options: ["近 6 个月", "近 12 个月", "近 24 个月", "全部数据"].join("\n"),
		default: "近 24 个月",
		change: () => load(true),
	});
	page.add_inner_button(__("立即刷新"), () => load(true));
	page.add_inner_button(__("全屏展示"), () => toggleFullscreen());
	page.add_menu_item(__("缩放模式：贴合 / 原始"), () => toggleFit());
	page.set_indicator(__("实时"), "green");

	buildSkeleton(page);
	fitCanvas();
	$(window).on("resize", fitCanvas);

	startClock();
	load(true);
	restartTimer();

	// 页面隐藏时暂停轮询，回到前台立刻补一次 —— 大屏常年开着，别白压数据库
	document.addEventListener("visibilitychange", () => {
		if (document.hidden) {
			stopTimer();
		} else {
			load(true);
			restartTimer();
		}
	});

	/* ------------------------------------------------------------------ */
	function setupStyles() {
		if ($("#gi-style").length) return;
		$("<style>", { id: "gi-style" }).text(GI_CSS).appendTo("head");
	}

	function buildSkeleton(page) {
		const { pad, gap, headerH, kpiH, rowBH, rowCH, footerH } = GI_LAYOUT;
		const html = `
		<div class="gi-loading" id="gi-loading" style="display:none;">${__("数据加载中…")}</div>
		<div class="gi-canvas" style="width:${GI_LAYOUT.canvasW}px;height:${GI_LAYOUT.canvasH}px;padding:${pad}px;">
			<div class="gi-head" style="height:${headerH}px;">
				<div>
					<div class="gi-title">GROWTH INSIGHT · 用户增长数据大屏</div>
					<div class="gi-sub" id="gi-sub">${__("加载中…")}</div>
				</div>
				<div class="gi-clock">
					<div class="t" id="gi-time">--:--:--</div>
					<div class="d" id="gi-date">-</div>
				</div>
			</div>

			<div class="gi-row" style="height:${kpiH}px;margin-bottom:${gap}px;" id="gi-kpis"></div>

			<div class="gi-row" style="height:${rowBH}px;margin-bottom:${gap}px;">
				${panelMarkup(3, "区域分布（Top 8）", '<div id="gi-region"></div>')}
				${panelMarkup(6, "月度增长趋势", '<div id="gi-trend"></div>')}
				${panelMarkup(3, "渠道质量 vs 留存", '<div id="gi-channel"></div>')}
			</div>

			<div class="gi-row" style="height:${rowCH}px;">
				${panelMarkup(3, "套餐构成（当前在服）", '<div id="gi-plan"></div>')}
				${panelMarkup(3, "生命周期分布", '<div id="gi-tenure"></div>')}
				${panelMarkup(3, "自动结论 & 转化漏斗", '<div id="gi-funnel"></div>')}
				${panelMarkup(3, "实时事件流", '<div id="gi-events"></div>')}
			</div>

			<div class="gi-foot" style="height:${footerH}px;margin-top:${gap}px;">
				<span id="gi-foot-left">${__("口径：趋势基于全量历史；下钻基于统计区间内新增 cohort")}</span>
				<span id="gi-foot-right"></span>
			</div>
		</div>`;
		$(page.body).append(html);
	}

	function panelMarkup(span, title, inner) {
		const width = GI_PANEL_W(span);
		return `<div class="gi-panel" style="flex:0 0 ${width}px;">
			<div class="gi-panel-title">${__(title)}</div>
			<div class="gi-panel-body">${inner}</div>
		</div>`;
	}

	/* ------------------------------------------------------------------ */
	function toggleFit() {
		$(page.body).find(".gi-canvas").toggleClass("gi-fit-off");
		fitCanvas();
	}

	function fitCanvas() {
		const host = $(page.body);
		const canvas = host.find(".gi-canvas");
		if (!canvas.length) return;
		if (canvas.hasClass("gi-fit-off")) {
			canvas.css("transform", "none");
			host.css({ overflow: "auto" });
			return;
		}
		host.css({ overflow: "hidden" });
		const availW = host.width() - 4;
		const availH = host.height() - 4;
		const scale = Math.min(availW / GI_LAYOUT.canvasW, availH / GI_LAYOUT.canvasH);
		state.scale = scale;
		// 水平居中
		const scaledW = GI_LAYOUT.canvasW * scale;
		const offsetX = Math.max((availW - scaledW) / 2, 0);
		canvas.css("transform", `translateX(${offsetX}px) scale(${scale})`);
	}

	function toggleFullscreen() {
		const el = $(page.body)[0];
		if (!document.fullscreenElement) {
			(el.requestFullscreen || el.webkitRequestFullscreen).call(el);
		} else {
			document.exitFullscreen();
		}
	}

	function startClock() {
		const tick = () => {
			const now = new Date();
			$("#gi-time").text(
				`${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(
					2,
					"0"
				)}:${String(now.getSeconds()).padStart(2, "0")}`
			);
			$("#gi-date").text(
				`${now.getFullYear()}年${now.getMonth() + 1}月${now.getDate()}日 · ${__("周")}${
					["日", "一", "二", "三", "四", "五", "六"][now.getDay()]
				}`
			);
		};
		tick();
		state.tick = setInterval(tick, 1000);
	}

	/* ------------------------------------------------------------------ */
	function currentFilters() {
		const map = {
			"近 6 个月": 6,
			"近 12 个月": 12,
			"近 24 个月": 24,
			全部数据: 1200,
		};
		const months = map[rangeField.get_value()] || 24;
		const from = new Date();
		from.setMonth(from.getMonth() - months);
		return {
			from_date: from.toISOString().slice(0, 10),
			to_date: new Date().toISOString().slice(0, 10),
		};
	}

	function load(force) {
		if (state.loading) return;
		state.loading = true;
		$("#gi-loading").show();
		return frappe
			.call({
				method: "growth_insight.api.dashboard.get_dashboard_data",
				args: {
					filters: currentFilters(),
					use_cache: force ? 0 : 1,
				},
			})
			.then((r) => {
				state.lastRendered = r.message;
				render(r.message);
			})
			.catch((err) => {
				console.error("[growth_insight] dashboard load failed", err);
				frappe.msgprint({
					title: __("加载失败"),
					indicator: "red",
					message: __("无法加载增长数据，请确认已分配 Growth Viewer 角色"),
				});
			})
			.always(() => {
				state.loading = false;
				$("#gi-loading").hide();
			});
	}

	function restartTimer() {
		stopTimer();
		restartCountdown();
		state.timer = setInterval(() => {
			if (!document.hidden) load(false);
			restartCountdown();
		}, 60000);
	}

	function restartCountdown() {
		state.countdown = 60;
		clearInterval(state.countdownTimer);
		state.countdownTimer = setInterval(() => {
			if (--state.countdown <= 0) return;
			$("#gi-foot-right").text(`${state.countdown}s ${__("后自动刷新")}`);
		}, 1000);
	}

	function stopTimer() {
		clearInterval(state.timer);
		clearInterval(state.countdownTimer);
		state.timer = null;
	}

	/* ------------------------------------------------------------------ */
	function render(data) {
		if (!data) return;
		const kpi = data.kpi || {};
		renderKpis(kpi);
		renderRegion(data.region || [], GI_PANEL_W(3) - 32);
		renderTrend(data.trend || { rows: [] }, GI_PANEL_W(6) - 32);
		renderChannel(data.channel || [], GI_PANEL_W(3) - 32);
		renderPlan(data.plan || [], GI_PANEL_W(3) - 32);
		renderTenure(data.tenure || [], GI_PANEL_W(3) - 32);
		renderFunnel(data.funnel || [], GI_PANEL_W(3) - 32);
		renderEvents(data.events || []);

		const scope = data.scope || {};
		$("#gi-sub").text(
			`${__("统计区间")} ${scope.from_date || "-"} ~ ${scope.to_date || "-"} ｜ ${__(
				"当前 Cohort"
			)} ${giInt(scope.record_count)} ${__("条")} ｜ ${__("趋势基线")} ${giInt(
				scope.trend_baseline_count
			)} ${__("条")}`
		);
		$("#gi-foot-right").text(`${__("数据更新时间")} ${giEsc(data.generated_at || "-")}`);
	}

	function renderKpis(kpi) {
		const cards = [
			{
				label: __("累计开通"),
				value: giInt(kpi.total_acquired),
				color: GI_COLORS.indigo,
				icon: "◫",
				delta: `近 30 天 +${giInt(kpi.new_last_period)}`,
			},
			{
				label: __("当前活跃用户"),
				value: giInt(kpi.active_users),
				color: GI_COLORS.accent,
				icon: "◉",
				delta: `占开通 ${giPct(
					(giNum(kpi.active_users) / Math.max(giNum(kpi.total_acquired), 1)) * 100
				)}`,
			},
			{
				label: __("累计流失"),
				value: giInt(kpi.churned_users),
				color: GI_COLORS.red,
				icon: "⊘",
				delta: `历史流失率 ${giPct(kpi.total_churn_rate)}`,
			},
			{
				label: __("近 30 天净增"),
				value: giInt(kpi.net_last_period),
				color: giNum(kpi.net_last_period) >= 0 ? GI_COLORS.green : GI_COLORS.red,
				icon: "↗",
				delta: `新增 ${giInt(kpi.new_last_period)} / 流失 ${giInt(kpi.churned_last_period)}`,
			},
			{
				label: __("月度经常性收入 MRR"),
				value: giMoney(kpi.mrr),
				color: GI_COLORS.amber,
				icon: "¥",
				delta: `ARR ${giMoney(kpi.arr)}`,
			},
			{
				label: __("ARPU / 平均生命周期"),
				value: giMoney(kpi.arpu),
				color: GI_COLORS.purple,
				icon: "◎",
				delta: `ARPU ${giMoney(kpi.arpu)} · ${giNum(kpi.avg_lifetime_months).toFixed(
					1
				)} ${__("月")}`,
			},
		];
		const width = `flex:1;height:${GI_LAYOUT.kpiH}px;`;
		$("#gi-kpis").html(
			cards
				.map((c) => {
					const style = `${width}margin-right:${GI_LAYOUT.gap}px;`;
					return `<div class="gi-panel" style="${style}">
					<div class="gi-panel-body gi-kpi" style="padding:0 18px;">
						<div class="ico" style="background:${c.color}22;color:${c.color};">${c.icon}</div>
						<div>
							<div class="num" style="color:${c.color};">${c.value}</div>
							<div class="lab">${c.label}</div>
							<div class="delta" style="color:${GI_COLORS.textDim};">${c.delta}</div>
						</div>
					</div>
				</div>`;
				})
				.join("")
		);
		// 去掉最后一个卡片的右边距，保持右边缘对齐
		$("#gi-kpis .gi-panel:last-child").css("margin-right", 0);
	}

	function renderRegion(rows, width) {
		const top = rows.slice(0, 8);
		$("#gi-region").html(
			giBarList(
				top,
				(r) => r.region,
				(r) => r.active,
				{ suffixFn: (r) => ` / ${giInt(r.total)}` }
			)
		);
	}

	function renderTrend(trend, width) {
		$("#gi-trend").html(giTrendChart(trend.rows, width, GI_LAYOUT.rowBH - 84));
	}

	function renderChannel(rows, width) {
		const html = giBarList(
			rows.slice(0, 8),
			(r) => r.channel,
			(r) => r.total,
			{
				colorFn: (r) =>
					giNum(r.churn_rate) >= 35
						? GI_COLORS.red
						: giNum(r.churn_rate) >= 22
						? GI_COLORS.amber
						: GI_COLORS.green,
				suffixFn: (r) => `流失 ${giPct(r.churn_rate)}`,
			}
		);
		$("#gi-channel").html(html);
	}

	function renderPlan(rows, width) {
		$("#gi-plan").html(giDonut(rows, "plan", "active", Math.min(width, 190), 190));
	}

	function renderTenure(rows, width) {
		$("#gi-tenure").html(
			giColumnChart(rows, "bucket", [
				{ key: "active", label: "在服", color: GI_COLORS.green },
				{ key: "churned", label: "已流失", color: GI_COLORS.red },
			], width, GI_LAYOUT.rowCH - 96)
		);
	}

	function renderFunnel(rows, width) {
		let insightsHtml = "";
		frappe
			.call({ method: "growth_insight.api.dashboard.get_insights" })
			.then((r) => {
				const list = r.message || [];
				insightsHtml = `<div class="gi-insights">${list
					.map(
						(item) =>
							`<div class="gi-insight ${
								item.type === "warning" ? "warn" : item.type === "success" ? "ok" : ""
							}"><span>${item.type === "warning" ? "⚠" : "◈"}</span><span>${giEsc(
								item.text
							)}</span></div>`
					)
					.join("")}</div>`;
				$("#gi-funnel").html(
					`<div class="gi-panel-title" style="padding:0 0 6px;">${__("转化漏斗")}</div>` +
						giFunnel(rows, width, 150) +
						`<div style="margin-top:8px;font-size:13px;font-weight:700;">${__(
							"自动结论"
						)}</div>` +
						insightsHtml
				);
			})
			.catch(() => {
				$("#gi-funnel").html(giFunnel(rows, width, 260));
			});
	}

	function renderEvents(events) {
		if (!events || !events.length) {
			$("#gi-events").html('<div class="gi-empty">' + __("暂无事件") + "</div>");
			return;
		}
		const itemHtml = (e) => `
			<div class="gi-event ${e.type === "开通" ? "new" : "lost"}">
				<span style="color:${e.type === "开通" ? GI_COLORS.green : GI_COLORS.red};">●</span>
				<span style="color:${GI_COLORS.textDim};">${giEsc(String(e.date).slice(5))}</span>
				<span>${giEsc(e.user_name)}</span>
				<span style="color:${GI_COLORS.textDim};">${giEsc(e.plan)}·${giEsc(e.region)}</span>
				<span class="rp">${e.type === "开通" ? "+" : "-"}${giEsc(e.type)}</span>
			</div>`;
		const list = events.map(itemHtml).join("");
		// 复制一份实现无缝滚动（marquee 标准做法）
		$("#gi-events").html(
			`<div class="gi-events"><div class="gi-events-track">${list}${list}</div></div>`
		);
	}
};
