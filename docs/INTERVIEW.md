# 面试讲述稿与高频追问应对（Growth Insight）

> 用途：把这次交付讲清楚、讲专业，并提前准备好面试官最可能追问的点。
> 配套代码在仓库根 `growth_insight/`，在线仓库 https://github.com/yangpengfei6/mianshi。

---

## 一、3 分钟项目讲述稿（照着念即可）

"我交付的是一个 **Frappe 自定义 App —— Growth Insight 用户增长分析**，对应笔试的三项要求：
一个用户服务开通/流失的**单据 DocType**、一个 7 视角的**增长分析报表**、一块**用户增长大屏**。

技术上的核心取舍是：我把**所有指标算法抽到一个零 Frappe 依赖的纯 Python 引擎** `analytics/core.py` 里，
报表、大屏、REST API 三条出口**共用同一份聚合逻辑**，保证无论在哪看数，口径都一致——
这是增长看板最容易翻车的地方。

为了在本机没有 Docker/bench 的情况下也能验证，我用 **pytest 真跑数据链路 + 一份最小 frappe 桩**做离线集成冒烟，
CI 里 75 个测试全绿，包括：字段契约（DocType 字段 ↔ 代码取数字段一一对应）、
报表返回结构、风险分分布、控制器校验、接口权限。

Mock 数据不是随机造的，而是用**生存模型（离散时间 hazard）**模拟真实 SaaS 的流失曲线：
渠道质量分层、买量扩盘的副作用、早流失期都复现出来了，所以图表是'能讲故事'的数据，而不是均匀噪声。"

---

## 二、架构一图流

```
生成/真实数据
      │
      ▼
growth_insight/mock/generator.py   ← 生存模型产出 798 条可复现 Mock 数据
      │
      ▼
growth_insight/analytics/service.py   ← Frappe ORM 取数 + 归一化（唯一碰 DB 的层）
      │
      ▼
growth_insight/analytics/core.py   ← 纯 Python 指标引擎（趋势/留存/渠道/套餐/漏斗/自动结论）
      │
      ├─► report/user_growth_analytics.py   （Script Report，7 视图）
      ├─► page/growth_screen.js             （大屏，手写 SVG，零 CDN）
      └─► api/dashboard.py                  （大屏数据接口，带角色鉴权 + 短 TTL 缓存）
```

**一句话**：service 是唯一和 Frappe 打交道的层，core 是纯逻辑，三个出口只是 core 的不同"外壳"。

---

## 三、高频追问 & 标准回答

### Q1：为什么要把分析逻辑单独抽出来，不直接在 Report 里写 SQL？
"三个出口（报表、大屏、API）要给同一批人看，口径必须一致。
如果各写各的，最容易出现的就是'报表说涨、大屏说跌'。
抽到 core 后：① 单一事实来源；② 不依赖数据库就能 pytest 断言，CI 秒级跑完；
③ 以后数据源换成数仓/Insights，只改 service 一层，引擎不动。"

### Q2：流失风险分（risk_score）是怎么算的？为什么有人的分是满分？
"以 `last_active_on`（最后活跃日）为锚点，算'沉默天数 / 该套餐健康活跃间隔'。
免费试用间隔 30 天、企业版 120 天——套餐越贵，容忍越久不活跃。
已流失用户直接给 100 分；在服用户按沉默程度线性逼近 100。
**这里有个我专门修过的坑**：最早 generator 没生成 `last_active_on`，
导致所有在服用户都回退到'按开通日算沉默'，结果全员 100 分，风险分失去意义——
现在已用契约测试锁死这个回归。"

### Q3：当前月的数据为什么看起来'偏低'？
"当前月还没走完，新增/流失天然不完整。我在趋势里给末月打了 `is_partial` 标记，
并且在报表顶部明确提示'部分月数据请勿与完整月份直接比较'。
这是增长看板最常见的误导点，我专门做了处理而不是假装没看见。"

### Q4：报表和大屏的数字，你怎么保证对得上？
"它们都调 `service.get_analysis()`，而 `get_analysis` 内部只调 `core` 的同一组函数。
不存在两套算法，所以对得上。CI 里 `test_report_all_views_return_six_tuple` 等用例就是锁这个契约的。"

### Q5：Mock 数据看起来会不会很假？
"用一个简化版生存模型：按渠道/套餐/生命周期给不同的 hazard（流失风险），
再叠加月度增长趋势、季节性、活动脉冲。效果上能复现真实 SaaS 的几个特征：
转介绍渠道最稳、付费广告最差（相差 3 倍以上）；免费试用流失率最高；
早流失期（前 3 个月）系数最高。流失说明这类人工字段只给约 45% 的流失用户填，
避免'每条都一样'的假数据感。种子固定 `20261014`，可复现。"

### Q6：权限怎么做的？Desk Page 本身不做角色隔离吧？
"对，Frappe 的 Page 路由任何登录用户都能敲，所以**权限卡在接口层** `api/dashboard.py`：
校验 `Growth Analyst / Growth Viewer / System Manager` 角色，或对该单据有读权限，否则抛 `PermissionError`。
同时结果做 60 秒短 TTL 缓存，避免大屏常亮时被报表查询打爆数据库。"

### Q7：版本兼容怎么处理？（v15 / v16）
"命名用 Controller 的 `autoname()` 而不是 DocType 的 `format:{MM}`——后者在 v16 已废弃，
写成方法可同时兼容 v14~v16。其余按官方推荐写法，CI 在 Python 3.11/3.12 下验证。"

### Q8：如果要上生产，你还会做什么？
"① 把 `service.py` 的取数换成真实数据管线（引擎不动）；
② 风险分已由 `tasks/daily.py` 的定时任务每晚批量刷新，生产直接挂 scheduler；
③ 大屏目前的'实时事件流'是按日期倒序抽最近 30 条，生产可换成消息队列/CDC 增量；
④ 加真实性能压测（800 条没问题，但百万级要走 `frappe.db.get_all` 分页 + 聚合下推）。"

---

## 四、现场演示动线（给面试官看什么）

1. **报表**：Desk → 报表列表 → `User Growth Analytics`，依次切 7 个视图，重点看"增长趋势"顶部口径说明。
2. **大屏**：`/app/growth-screen`，深色科技风，左侧 KPI、中部趋势/渠道、右侧套餐构成/漏斗/自动结论/事件流。
3. **数据**：安装后自动灌入 **798 条** Mock 数据（可在 DocType 列表搜 `USR-` 查看）。
4. **代码**：打开 `analytics/core.py`（纯逻辑）+ `analytics/service.py`（唯一碰 DB 的层）+ `tests/`（75 个测试）。
5. **CI**：GitHub Actions 全绿，含离线 frappe 桩集成冒烟。

---

## 五、文件速查

| 想看什么 | 去哪 |
|---|---|
| 指标算法（纯 Python） | `growth_insight/analytics/core.py` |
| 取数适配层（唯一碰 Frappe 处） | `growth_insight/analytics/service.py` |
| Mock 数据生成（生存模型） | `growth_insight/mock/generator.py` |
| 单据控制器（校验/派生/事件流） | `growth_insight/growth_analytics/doctype/user_service_record/user_service_record.py` |
| 报表（7 视图） | `growth_insight/growth_analytics/report/user_growth_analytics/` |
| 大屏（手写 SVG） | `growth_insight/growth_analytics/page/growth_screen/growth_screen.js` |
| 数据接口（权限+缓存） | `growth_insight/api/dashboard.py` |
| 定时刷新风险分 | `growth_insight/tasks/daily.py` |
| 离线集成冒烟测试 | `growth_insight/tests/test_frappe_contract.py` + `_frappe_stub.py` |
| 设计说明 | `docs/DESIGN.md` |
