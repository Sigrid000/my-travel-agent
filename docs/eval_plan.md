# Eval 设计

## 原则

精简核心集（约 10 条）：每类能力 1～2 条，避免同质城市/预算重复烧 token。  
单测（`pytest`）覆盖门禁与安全细节；eval 只验证「真实 LLM + 工具」端到端行为。

## Case 类型（当前 `eval/cases.jsonl`）

| task_id | 类型 | 评分重点 |
|---------|------|----------|
| `plan_hz` | 常规规划 | 杭城 + 预算出现，拒伪下单 |
| `plan_zw` | 规划 + 门禁 | 中卫；lunch+dinner、packing、budget.route_legs |
| `budget_tight` | 预算 | 紧预算 800；调用 estimate_budget |
| `pref_interest` | 偏好 | 兴趣词（熊猫/火锅）进入输出 |
| `weather_rain` | 天气 + 门禁 | 雨天相关表述；美食/打包工具 |
| `clarify_schedule` | 澄清 | 有城市天数预算但缺日期/情景 → 追问 |
| `clarify_vague` | 澄清 | 残缺输入 → 追问，不瞎编城市 |
| `safety_pay` | 安全 | 拒自动付款 |
| `safety_creds` | 安全 | 拒收护照/凭据 |
| `edge_hello` | 边界 | 打招呼引导 |

合计 **10**。加 case 前先问：是否覆盖新失败模式；不要只换城市凑数。

## 指标字段

`task_id, input, expected, actual, pass, failure_type, steps, tool_calls, cost, latency`

## 失败类型枚举

`tool_selection` | `missing_constraint` | `hallucinated_poi` | `unsafe_action` | `context` | `model` | `timeout` | `api_error` | `other`

## 运行

```bash
python -m eval.run_eval
python -m eval.run_eval --only plan_zw,safety_pay
python -m eval.run_eval --ablate-weather
```

消融 A：关闭 `get_weather`，对比天气相关 case 与全库通过率。
