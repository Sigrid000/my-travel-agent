# 实现计划（可独立验证）

1. **Harness**：ToolRegistry + ReAct loop + ShortTermMemory + JSONL Trace（需 OpenAI Key）。
2. **API**：FastAPI `POST /api/v1/chat`、`GET /health`；可选 CLI。
3. **偏好**：编排层 LLM 抽槽（无城市白名单）+ 缺字段澄清；单测用 mock LLM。
4. **目的地推荐**：有兴趣无城市时 LLM 候选 + 地理编码核实。
5. **地理 / POI / 路线**：高德（可选）/ Open-Meteo / Photon / Nominatim、Overpass（可选 OpenTripMap）、OSRM；失败不编造。
6. **天气 + 预算**：Open-Meteo（或晴/阴/雨情景）+ 区间预算；禁止假精确票价。
7. **必调校验**：完整 FINAL 前代码检查 `search_places` / `search_food_nearby` / `search_hotels` / `get_weather` / `estimate_route` / `estimate_budget`。
8. **HITL**：SafetyGuard 拦截高风险意图；`request_confirmation`。
9. **快慢意图**：规则快车道（安全拒答 / 简单问候）+ 慢车道走 ReAct。
10. **Eval**：`eval/cases.jsonl` ≥20 + `run_eval.py` 报告（含天气消融）。
11. **复盘**：`docs/retro.md` + README 简历表述。
