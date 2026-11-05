# Spec：休闲旅行规划 Agent

## 目标用户

自由行游客（1–5 人），需要在预算与天数约束下得到可执行、可解释的城市行程建议。

## 触达渠道

- HTTP API：`POST /api/v1/chat`（主入口）
- CLI：`python -m app.cli "自然语言需求"`（可选）

## 核心任务

1. 用 LLM 将自然语言抽成旅行约束（城市、天数、预算、兴趣、同行、日程依据）。
2. 无城市但有兴趣时：推荐候选目的地（LLM 提出 → 地理编码核实 → 用户点选）。
3. 调用只读外部工具查 POI / 地理编码 / 路线 / 天气，并估算预算区间。
4. 生成按日 Markdown 行程（含取舍说明与数据来源）；工具失败则不编造。
5. 对付款、凭据、自动下单等高风险请求拒绝执行，仅给出确认选项。

## MVP 对「衣食住行」的覆盖（诚实边界）

理想产品应覆盖衣食住行；**当前 MVP 已覆盖市内衣食住行游的可解释规划**，城际大交通与真实成交价仍刻意不做：

| 维度 | MVP 现状 | 说明 |
|------|----------|------|
| **行（市内）** | 有 | `estimate_route`（高德优先）+ 打车经验区间 |
| **行（城际）** | 无 | 不查机票/高铁真实班次与票价（后续有正规 API 再接） |
| **食** | 有 | 午餐贴景点 + 晚饭全市参考；人均来自高德（有则写）；不代订 |
| **住** | 有 | `search_hotels` 周边候选 + 价位参考；HITL，不代订 |
| **衣** | 有 | `suggest_packing` 规则清单（天气/兴趣）；非购物推荐 |
| **游（景点）** | 有 | `search_places`（点位/电话/开放时间）；门票→OTA 深链 + 经验区间 |

后续增量见下方「备注」。

## 工具与编排

| 能力 | 权限 | 失败 / 策略 |
|------|------|-------------|
| LLM 偏好抽槽（编排层） | — | 未提及的字段保持 null 并追问；不编造城市/预算 |
| 目的地推荐（编排层） | — | 有兴趣无城市：LLM 候选 → 地理编码核实 → 用户点选 |
| `search_places` | low | 有 `AMAP_API_KEY` 时优先高德；否则 Overpass；失败不编造 POI |
| `search_food_nearby` | low | **完整行程必调**；按景点 lat/lon 周边餐饮 |
| `search_hotels` | low | **完整行程必调**；候选列表，不代订 |
| `geocode_location` | low | 有 Key 时优先高德，否则 Open-Meteo → Photon → Nominatim |
| `estimate_route` | low | **完整行程必调**；高德优先，驾车附打车区间 |
| `get_weather` | low | **完整行程必调**；Open-Meteo 或用户天气情景 |
| `estimate_budget` | low | **完整行程必调**；有路线/人均时须带 route_legs / food_cost_hints（代码校验） |
| `suggest_packing` | low | **完整行程必调**；建议携带规则清单 |
| `request_confirmation` | high | 不执行副作用；只返回待确认选项 |
| `collect_preferences` | low | 弱辅助说明；完整解析在编排层 LLM |
| `echo` | low | 开发/评测用 |

完整行程 FINAL 前，代码要求：已调用 `search_places`、`search_food_nearby`（至少 lunch+dinner）、`search_hotels`、`get_weather`、`estimate_route`、`estimate_budget`、`suggest_packing`；预算参数与工具结果对齐；FINAL 含门票核验/住宿/美食参考/预算/建议携带/数据来源等章节。

## Human-in-the-loop

必须拦截并要求用户自行确认/跳转官方平台：

- 付款、预订、取消订单
- 身份证/护照/银行卡/账号密码
- 不可退款条款代签

## 成功标准

- clone 后按 README 能启动 API 并完成一次规划对话
- 有 JSONL trace（tool / latency / token）
- ≥20 eval case；报告通过率与失败类型
- 安全用例：要求自动付款时不得「已下单」

## 成本约束

- 单 trajectory：`max_steps ≤ 20`，`max_tool_calls ≤ 30`
- 需要配置 `OPENAI_API_KEY`；评测与演示走真实模型

## 备注（已落地 vs 后续）

**原则不变**：不做半成品本地攻略库（不维护全域 tips/白名单假清单）；经验类提醒依赖工具结果与模型说明，并标明不确定性。

**MVP 已落地（勿再写成待办）**：

- **食**：`search_food_nearby`（lunch 贴景点 / dinner 全市）+ 人均字段（高德有则返回）+ FINAL「美食参考」；不代订。
- **住**：`search_hotels` 候选 + 距离/评分；价位多为参考档，预订 HITL。
- **衣**：`suggest_packing` 规则清单，FINAL「建议携带」必含。
- **游/票**：景点工具 + `ticket_verify`（携程/美团搜索深链）；不编造精确票价。

**仍属后续（非本 MVP）**：

- **行（城际）**：火车/航班信息查询（有正规 API / 商务合作再接）；禁止自动出票。
- **用户笔记检索**：粘贴攻略优先排程（轻量检索，不上 Milvus）。
- **精确门票/房价成交价**：依赖官方或 OTA 签约接口，当前仅深链 + 区间。
