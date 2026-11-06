# Trace 约定

每次对话/评测会在 `TRACE_DIR`（默认 `traces/`）写入 JSONL；完整行程 FINAL 另存同名 `.final.md`。

字段说明见同目录旁的机器可读 schema：[`trace-schema.json`](../app/observability/trace-schema.json)。

## 文件

| 文件 | 内容 |
|------|------|
| `{trace_id}.jsonl` | 一行一个事件（UTF-8 JSON） |
| `{trace_id}.final.md` | **完整** FINAL 正文（不截断）；仅在产出最终行程时生成 |

`llm_response.content_preview`、`tool_result.result_preview` 仍可截断，避免把整段工具 JSON 打爆上下文日志；复盘行程以 `final` / `.final.md` 为准。

## 公共字段（每行）

| 字段 | 类型 | 说明 |
|------|------|------|
| `ts` | string | ISO-8601 UTC |
| `trace_id` | string | 本轨迹 ID |
| `session_id` | string | 会话 ID |
| `kind` | string | 事件类型，见下表 |

## `kind` 一览

| kind | 主要额外字段 | 说明 |
|------|----------------|------|
| `user_message` | `content` | 用户原文 |
| `intent` | `intent`, `lane`, `reason` | 快/慢意图 |
| `safety_block` | `reason` | 进 LLM 前拦截 |
| `preferences` | `summary`, `missing`, `weather_scenario`, … | 抽槽结果 |
| `clarify` | `missing` | 缺字段追问 |
| `destination_suggest` | `ok`, `candidates`, `error` | 无城市有兴趣时的候选城 |
| `prefetch` | `ok_geocode`, `ok_weather`, `center`, `tools` | 进 ReAct 前预取地理编码/天气（计入门禁，不计 agent step） |
| `llm_request` | `step`, `prompt_chars` | 一步开始 |
| `llm_response` | `step`, `content_preview`, `prompt_tokens`, `completion_tokens` | 模型回复摘要（预览可截断） |
| `agent_step` | `step` | 一步标记 |
| `tool_result` | `tool`, `args`, `result_preview` | 工具调用（结果预览可截断） |
| `final_rejected` | `failures`, `step` | 门禁拒绝 FINAL |
| `budget_adjust` | `kind`, `gap_cny`, `target_style`, `transport_mode`, `step` | 预算过紧/过宽触发的一轮反馈调整 |
| `final` | `content`, `content_chars` | **完整**最终回复 |

## 读取示例

```bash
# 看完整行程
type traces\20261105_065557_8404470f.final.md

# 从 JSONL 抽 final
python -c "import json; from pathlib import Path
p=Path('traces')/ 'YOUR_ID.jsonl'
for line in p.read_text(encoding='utf-8').splitlines():
    e=json.loads(line)
    if e.get('kind')=='final':
        print(e['content'][:200], '... chars', e.get('content_chars'))"
```
