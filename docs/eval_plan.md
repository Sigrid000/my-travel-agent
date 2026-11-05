# Eval 设计

## Case 类型

| 类型 | 数量目标 | 评分重点 |
|------|----------|----------|
| 常规规划 | 5 | 约束摘要、按日行程、来源 |
| 预算约束 | 4 | 预算区间、不超编造精确价 |
| 偏好约束 | 4 | 兴趣词进入行程 |
| 天气/变化 | 3 | 调用天气或声明备选 |
| 安全边界 | 4 | 拒付款/凭据/自动下单 |
| 空/残缺输入 | 2 | 追问或缺字段，不胡编城市 |

合计 ≥20。

## 指标字段

`task_id, input, expected, actual, pass, failure_type, steps, tool_calls, cost, latency`

## 失败类型枚举

`tool_selection` | `missing_constraint` | `hallucinated_poi` | `unsafe_action` | `context` | `model` | `timeout` | `other`

## 消融

- A：关闭 `get_weather`
- B：关闭预算硬提示（仍可调用 estimate_budget）

记录对「天气类 / 预算类」子集通过率影响。
