/**
 * 大屏渲染器离线验证脚本
 *
 * 用途：脱离 Frappe 环境，用真实数据跑一遍所有图表渲染函数，检查：
 *   1. 输出中不含 NaN / undefined / Infinity（手写 SVG 最典型的翻车点）；
 *   2. SVG 标签配对正确；
 *   3. 每个图表都真的产出内容而非空壳。
 *
 * 运行：node scripts/check_render.js [fixture.json]
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const FIXTURE = process.argv[2] || path.join(__dirname, "..", "_dash_fixture.json");
const PAGE_JS = path.join(
	__dirname,
	"..",
	"growth_insight",
	"growth_analytics",
	"page",
	"growth_screen",
	"growth_screen.js"
);

// 截取页面 JS 中"纯渲染函数"部分（on_page_load 之前），避免依赖 Frappe / jQuery
const source = fs.readFileSync(PAGE_JS, "utf8");
const MARKER = 'frappe.pages["growth-screen"].on_page_load';
const cutIndex = source.indexOf(MARKER);
if (cutIndex < 0) throw new Error("未找到 on_page_load 标记，脚本需同步更新");

const sandbox = {
	frappe: { provide: () => {} },
	__: (s) => s,
	console,
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(source.slice(0, cutIndex), sandbox);

const data = JSON.parse(fs.readFileSync(FIXTURE, "utf8"));

const checks = [];
function check(name, fn) {
	try {
		const html = fn();
		checks.push({ name, html });
	} catch (e) {
		checks.push({ name, error: e });
	}
}

const W = 890;
check("趋势图 giTrendChart", () => sandbox.giTrendChart(data.trend.rows, W, 300));
check("区域条形 giBarList(region)", () =>
	sandbox.giBarList(data.region, (r) => r.region, (r) => r.active, {
		suffixFn: (r) => ` / ${r.total}`,
	})
);
check("渠道条形 giBarList(channel)", () =>
	sandbox.giBarList(
		data.channel,
		(r) => r.channel,
		(r) => r.total,
		{ colorFn: (r) => (r.churn_rate >= 35 ? "#f87171" : "#34d399"), suffixFn: (r) => `流失 ${r.churn_rate}%` }
	)
);
check("套餐环形 giDonut", () => sandbox.giDonut(data.plan, "plan", "active", 190, 190));
check("生命周期 giColumnChart", () =>
	sandbox.giColumnChart(
		data.tenure,
		"bucket",
		[
			{ key: "active", label: "在服", color: "#34d399" },
			{ key: "churned", label: "已流失", color: "#f87171" },
		],
		423,
		276
	)
);
check("漏斗 giFunnel", () => sandbox.giFunnel(data.funnel, 423, 150));

// ---- 断言 ----
let failures = 0;
const BAD_TOKENS = ["NaN", "undefined", "Infinity", "null"];

for (const { name, error, html } of checks) {
	const problems = [];
	if (error) {
		problems.push(`抛出异常: ${error.message}`);
	} else {
		if (!html || html.length < 20) problems.push("输出过短，疑似空壳");
		for (const token of BAD_TOKENS) {
			if (html.includes(token)) problems.push(`输出包含非法 token: ${token}`);
		}
		// SVG 标签配对粗检（统计开/闭标签数量差）
		const svgCount = (html.match(/<svg[\s>]/g) || []).length;
		const svgClose = (html.match(/<\/svg>/g) || []).length;
		if (svgCount !== svgClose) problems.push(`<svg> 未配对: ${svgCount} vs ${svgClose}`);

		// 检查 SVG 数值属性里是否有非法值（加空格边界，避免命中 text-anchor="end" 这类属性）
		const numericAttrs =
			html.match(/(?:^|\s)(?:x|y|cx|cy|r|width|height|x1|y1|x2|y2)="([^"]*)"/g) || [];
		for (const attr of numericAttrs) {
			const value = attr.split('"')[1];
			if (value && /[a-zA-Z]/.test(value) && !/rotate|url|inherit/.test(value)) {
				problems.push(`数值属性混入非数字: ${attr}`);
			}
		}
	}

	if (problems.length) {
		failures++;
		console.log(`✗ ${name}`);
		problems.forEach((p) => console.log(`    ${p}`));
	} else {
		console.log(`✓ ${name}  (${html.length} bytes)`);
	}
}

console.log(`\n${checks.length - failures}/${checks.length} 项通过`);
process.exit(failures ? 1 : 0);
