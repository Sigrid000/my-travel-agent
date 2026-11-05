# 复盘（MVP）

## 为什么这样设计

- **自研轻量 ReAct harness**：对齐 AgentGuide「框架越大越好」误区；工具注册表可插拔，走真实 LLM API。
- **真工具优先、失败即报错**：有 Key 时高德优先；否则地理编码 Open-Meteo→Photon→Nominatim，POI Overpass；路线 OSRM；天气 Open-Meteo。失败不本地假坐标、不粗估路程、不编造景点。
- **偏好全靠 LLM 抽槽**：不维护城市/兴趣穷举表；无城市有兴趣时走「候选城 + 地图核实」。
- **必调工具代码校验**：不单靠 prompt，避免未调 route/budget 就输出完整行程。
- **SafetyGuard 快车道**：付款/凭据在进 LLM 前拦截，降低不安全输出与成本。
- **预算只给统一区间**：无实时报价 API，不做按城写死价目表。

## 替代方案

| 方案 | 未选原因 |
|------|----------|
| LangGraph / CrewAI | MVP 边界清晰前引入框架成本高 |
| 商旅假航班/自动 confirmed 预订 | 不符合 HITL，面试易被追问穿帮 |
| 上 Redis/Milvus / 本地 tips 库 | 半成品覆盖不全，易变成假知识；MVP 不做 |
| 正则穷举城市/兴趣 | 长尾穷举不完，与「不写死」原则冲突 |

## 消融

运行：

```bash
python -m eval.run_eval
python -m eval.run_eval --ablate-weather
```

基线需配置 `OPENAI_API_KEY` 后运行；结果写入 `eval/report.json`。  
`--ablate-weather` 可对比关闭天气工具后的通过率。

## 已知限制

- 评测与演示依赖真实模型，结果有波动，需抽检；改动后应重跑 eval。
- 公共 Overpass 忙线时 POI 可能暂时不可用（已对外部 HTTP 做失败后再试 1 次）。
- 单 Agent，无多子智能体分工（有意为之）。
- 市内衣食住行游已具备工具与 FINAL 门禁；城际大交通与真实成交价仍弱，见 spec「后续」。
