# AGENTS.md — my-travel-agent

## WHAT

休闲旅行规划 Agent：自然语言 → LLM 结构化约束 → 只读工具（POI/地理/路线/天气）→ 可解释行程；高风险动作 HITL。

## WHY（不可变约束）

- 不自动付款/预订/保存凭据
- 不编造无来源的 POI 与精确票价；预算用区间
- 不维护无法覆盖全域的本地 tips / 城市景点假清单
- 有 `AMAP_API_KEY` 时优先高德地理编码/POI；否则 Open-Meteo/Photon/Nominatim + Overpass。Nominatim 须带明确 User-Agent
- 不上 Redis/PG/Milvus（MVP）
- 不引入 LangGraph/CrewAI 作为核心依赖

## HOW

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000
pytest
python -m eval.run_eval
```

## TEST

- 改工具：补单测 + 相关 eval case
- 改 SafetyGuard：安全类 eval 必须仍通过
- 外部 API：单测 mock httpx；集成可选 `RUN_LIVE_TOOLS=1`
- Trace：见 `docs/trace.md`；改 `kind`/FINAL 落盘时更新 `app/observability/trace-schema.json`
