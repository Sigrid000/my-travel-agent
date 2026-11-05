# my-travel-agent

可写进简历的**休闲旅行规划 Agent**（Python）：自然语言 → LLM 抽槽约束 → 只读工具（地理/POI/路线/天气/预算）→ 可解释行程；付款/凭据 **HITL 拦截**；JSONL trace + 20+ eval。

对齐 AgentGuide「可写进简历的 Agent」实践与 Travel Agent 蓝图；工程模式参考商旅-agent-guide（注册表 / 快慢意图 / trace），**不复制假预订数据**。

## 功能

- 自研轻量 ReAct + `ToolRegistry`（不依赖 LangGraph/CrewAI）
- **偏好**：编排层 LLM 抽槽（城市/天数/预算/兴趣等），无城市白名单、无正则穷举
- **无城市仅有兴趣**（如「想看海」）：LLM 提候选目的地 → 地理编码核实 → 用户点选
- 工具：地理/POI/周边美食/酒店（优先高德）/ `estimate_route`（高德或 OSRM + 打车区间）/ `get_weather` / `estimate_budget`（可吃 route_legs）/ `request_confirmation`
- **完整行程代码校验**：必调工具（含 lunch+dinner 美食、`suggest_packing`）、预算参数对齐、FINAL 章节模板；不满足则拒绝 FINAL
- SafetyGuard：自动付款、代下单、凭据 → 拒绝执行
- 短时记忆 + `traces/*.jsonl`
- FastAPI：`POST /api/v1/chat`；CLI 可选
- Eval：`python -m eval.run_eval`（含天气消融）

## 快速开始

```bash
cd my-travel-agent
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# 填写 OPENAI_API_KEY（或 DeepSeek：改 BASE_URL / MODEL）后再启动
uvicorn app.main:app --reload --port 8000
```

示例：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat -H "Content-Type: application/json" -d "{\"message\":\"去杭州玩2天，预算2000，喜欢博物馆\"}"
```

CLI：

```bash
python -m app.cli "去杭州玩2天，预算2000，喜欢博物馆"
```

测试与评测：

```bash
pytest -q
python -m eval.run_eval
python -m eval.run_eval --ablate-weather
```

## 架构

```text
User -> FastAPI / CLI
     -> SafetyGuard (fast)
     -> Intent (fast/slow)
     -> LLM 偏好抽槽
     -> 缺城市且有兴趣 → 目的地推荐（LLM + 地理编码核实）
     -> 缺字段 → 澄清追问
     -> ReActLoop → ToolRegistry → 外部 API
     -> 必调工具代码校验 → FINAL
     -> ShortTermMemory + JSONL Trace
```

## 简历表述（可改数字）

> 构建休闲旅行规划 Agent：用 LLM 将自然语言抽成结构化约束；兴趣明确但未指定城市时提出候选目的地并经地理编码核实；接入高德（可选）/ Open-Meteo / Photon / Nominatim / Overpass(POI) / OSRM 等只读工具，生成带路线与预算区间的可解释行程；对完整行程做必调工具代码校验；付款/预订/凭据 HITL 拦截；自研 ReAct + 工具注册表，JSONL trace，20+ eval（含安全与天气消融）。

## 配置

见 `.env.example`。必须设置 `OPENAI_API_KEY`。景点默认走 OpenStreetMap Overpass（无需额外 Key）；`OPENTRIPMAP_API_KEY` 可选。

## 文档

- [docs/spec.md](docs/spec.md)
- [docs/prompt_plan.md](docs/prompt_plan.md)
- [docs/eval_plan.md](docs/eval_plan.md)
- [docs/retro.md](docs/retro.md)
- [AGENTS.md](AGENTS.md)
