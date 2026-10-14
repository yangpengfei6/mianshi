# Growth Insight — 用户增长分析套件（Frappe App）

面向 SaaS 用户增长的**开箱即用** Frappe 自定义 App：一张服务生命周期单据、一份多视角分析报表、一块可用于会议室投屏的数据大屏。

> 目标版本：**Frappe v15 / v16**。两个版本之间几处会踩坑的差异，已在 [版本兼容性](#七版本兼容性) 一节逐一说明。

---

## 一、交付内容（对应考题三项）

| # | 交付物 | 名称 | 访问路径 |
|---|--------|------|----------|
| 1 | **单据 DocType** | `User Service Record`（用户服务开通/流失档案）+ 子表 `Service Status Change` | Desk → `User Service Record`；安装自动灌入 **788 条** Mock 数据 |
| 2 | **报表 Report** | `User Growth Analytics`（Script Report，**7 个分析视角**） | Desk → 报表列表 → Growth Analytics；或 `/app/query-report/User Growth Analytics` |
| 3 | **大屏 Page** | `growth-screen`（1920×1080 深色科技风看板） | `/app/growth-screen` |

### 报表的 7 个视角
`增长趋势` · `Cohort 留存` · `区域分布` · `渠道质量` · `套餐表现` · `流失原因分析` · `明细数据`

### 大屏包含的模块
- 6 张 KPI 卡（累计开通 / 活跃 / 流失 / 净增 / MRR / ARPU）
- 月度增长趋势（柱 + 折线混合，自动标注"当月未走完"）
- 区域分布 Top8、渠道质量（按流失率着色）、套餐构成（环形图）
- 生命周期分布、用户价值漏斗、**后端自动生成的业务结论**、实时事件滚动流

---

## 二、快速开始

```bash
# 1. 取 App
bench get-app growth_insight https://github.com/<你的账号>/growth_insight.git
#   本地开发也可直接 bench get-app growth_insight ../growth_insight

# 2. 安装（会自动执行 before_install → DocType 同步 → Mock 数据灌入）
bench --site your.site install-app growth_insight

# 3. 构建前端资源
bench build --app growth_insight

# 4. 重启（若bench 以 developer 模式运行可省略）
bench restart
```

安装日志里会打印：`已生成 N 条用户服务档案`。

### 角色与权限

App 自带两个角色（安装时自动创建）：

| 角色 | 能力 |
|------|------|
| `Growth Analyst` | 读写服务档案、运行报表、查看大屏 |
| `Growth Viewer` | 只读（适合挂在会议室大屏账号上） |

> 大屏路由本身在 Frappe 里**不做角色隔离**（任何登录用户都能敲 URL），
> 因此真正的权限校验放在了数据接口层 `api/dashboard.py`。

### Mock 数据管理

```bash
# 重建（清空后重新生成）
bench --site your.site execute growth_insight.mock.loader.regenerate_mock_data --kwargs "{'force': 1}"

# 生产环境不想带 Mock 数据：site_config.json 里加
{"growth_insight_skip_mock_data": 1}
```

数据是**固定随机种子**生成的（seed=20261014），任何人安装后看到的完全一致。

---

## 三、项目结构

```
growth_insight/
├── growth_insight/
│   ├── analytics/
│   │   ├── constants.py     # 领域字典（Select 选项的唯一事实来源）
│   │   ├── core.py          # ★ 零 Frappe 依赖的聚合引擎（所有指标算法）
│   │   └── service.py       # 唯一的 Frappe ORM 出入口
│   ├── mock/
│   │   ├── generator.py     # ★ 零 Frappe 依赖的数据生成器（hazard 模型）
│   │   └── loader.py        # 写入数据库
│   ├── api/dashboard.py     # 大屏接口（含权限校验与缓存）
│   ├── tasks/daily.py       # 定时任务：刷新流失风险分
│   ├── setup/install.py     # before_install / after_install / after_migrate
│   ├── utils/helpers.py
│   ├── tests/               # 纯 Python 单元测试（CI 直接跑）
│   └── growth_analytics/    # Module 目录
│       ├── doctype/user_service_record/      # 单据 + Controller + Client Script + 测试
│       ├── doctype/service_status_change/    # 子表
│       ├── report/user_growth_analytics/     # 多视角报表
│       └── page/growth_screen/               # 大屏
├── docs/
│   ├── DESIGN.md            # ★ 设计说明与取舍（面试答辩建议读这篇）
│   └── preview.html         # 大屏静态预览（由 scripts/make_preview.js 生成，浏览器直接打开）
├── scripts/
│   ├── validate_repo.py     # 仓库完整性自检
│   ├── check_render.js      # 大屏图表离线渲染验证
│   └── make_fixture.py      # 生成前端测试夹具
└── .github/workflows/ci.yml
```

**架构原则：聚合公式只允许出现在 `analytics/core.py`。**
报表、大屏、API 三条出口都必须经过 `service.py` 调用 core，从架构上杜绝"报表和大屏数字打架"。

---

## 四、数据模型

### User Service Record（用户服务档案）

| 分组 | 字段 |
|------|------|
| 用户画像 | 用户编号、用户名称、联系邮箱、所属区域、城市、获客渠道、主要终端 |
| 订阅与开通 | 订阅套餐、MRR、LTV、服务开通日期、开通类型 |
| 生命周期 | 服务状态、生命周期(天)、**流失风险分**、最后活跃日期、流失日期、流失原因、流失说明 |
| 事件流水 | 状态变更记录（子表） |
| 备注 | 备注 |

### Service Status Change（状态变更流水，子表）
事件日期、事件类型（开通/续费/升级/降级/流失预警/流失/召回）、当时套餐、当时 MRR、说明

### 数据为什么不启用通用版本追踪？
`track_changes` 设为 `0`。理由：通用版本表会把每一次字段微调都记下来，
800 条档案 × 多次编辑 = 版本表爆炸、查询变慢。
这里改用**专用事件流水子表**，只记录与生命周期强相关的关键跃迁，
既能回答"有多少人曾经流失又被召回"，存储量也可控。

---

## 五、指标口径速查表

| 指标 | 定义 |
|------|------|
| 期初活跃 | 截至上月最后一天仍在服务中的用户数 |
| 新增 / 流失 | 开通日期 / 流失日期落在本月内的用户数 |
| 期末活跃 | 期初活跃 + 新增 − 流失 |
| 月度流失率 | 本月流失 ÷ **期初活跃** |
| MRR | 期末活跃用户的 MRR 之和 |
| ARPU | MRR ÷ 期末活跃数 |
| NRR（净收入留存） | （期初 MRR − 本月流失用户 MRR）÷ 期初 MRR |
| LTV | ARPU × 平均生命周期(月) |
| Cohort M*N* | 某开通月群体在第 N 个月末仍存活的比例 |
| 平均生命周期 | 流失用户按实际天数，在服用户统计到截止日 |

**两条容易踩坑的口径**（代码里都有显式处理）：

1. **趋势与下钻用不同数据集**：趋势必须基于全量历史（否则"期初活跃"会虚低），
   维度下钻才限定统计区间内的新增 cohort。
2. **当月是"部分月"**：趋势末月会用虚线标注并给出提示，
   不能与完整月份横向比较 —— 否则每个月初看板都会误报"流失暴涨"。

---

## 六、测试与质量保障

```bash
# 纯 Python 测试（无需 Frappe 环境），54 项
python -m pytest -q

# 仓库完整性自检（JSON 合法性、Select 选项与 constants 一致性、
# field_order 与 fields 是否匹配、hooks 路径是否可解析、ASCII 标识符护栏…）
python scripts/validate_repo.py

# 大屏图表离线渲染验证（用真实数据跑一遍所有图表，查 NaN/undefined/SVG 未配对）
python scripts/make_fixture.py && node scripts/check_render.js

# 生成大屏静态预览页（浏览器直接打开 docs/preview.html 即可看到效果）
node scripts/make_preview.js

# Frappe 单据测试（需要 bench 环境）
bench --site your.site run-tests --app growth_insight
```

> 关于本项目开发与上述验证在同一台无法启动 Frappe 环境的机器上完成，
> 因此特意把**分析引擎与 Mock 生成器做成零 Frappe 依赖**，
> 使得最核心的逻辑可以用 pytest 真实断言，而不是靠"看起来对"。

---

## 七、版本兼容性

| 关注点 | 处理方式 |
|--------|----------|
| DocType 命名 `format:{MM}` 在 v16 **已废弃** | 改用 Controller 的 `autoname()` 方法，v14~v16 通吃 |
| `export` 权限字段某些版本不存在 | 未使用，避免写入 DocPerm 未知列报错 |
| Workspace fixture 的 `content` 结构各版本仍在演进 | **不随 App 发布 Workspace**（改为路由访问 + 报表内跳转），见 `docs/DESIGN.md` §6 |
| 自定义角色 `Growth Analyst` / `Growth Viewer` | 在 `before_install`（DocType 同步之前）创建，避免权限表 Link 校验失败 |

---

## 八、已知限制与后续演进

- **时区**：全部按站点本地日期（`frappe.utils.getdate`）计算，未做多时区归一。
- **数据量**：聚合在 Python 侧完成，万级记录无压力；到十万级建议换成 SQL 窗口函数或接入 Frappe Insights。
- **缺少真实活跃埋点**：`risk_score` 目前基于 `last_active_on` 与套餐经验阈值，接真实埋点后可换成 ML 流失预测。
- **未实现**：用户级 Portal、报表导出 PDF / 定时邮件推送、多渠道归因（目前仅记录来源渠道，未做归因权重分摊）。

---

## License

MIT
