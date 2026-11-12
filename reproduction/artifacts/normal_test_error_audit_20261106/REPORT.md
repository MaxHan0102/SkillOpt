# 普通测试异常核查（非 avg@5）

核查时间：2026-11-06T18:20:32+08:00

来源：`/root/max/SkillOpt/reproduction/outputs/terra_high_four_20261105`。只检查 baseline / best / final 的完整测试集，不包含训练、验证、smoke、avg@5 或异常补测。

| Benchmark | Skill | 测试数 | 答对 | 正常完成但不正确 | 最终 API 通信/服务失败 | 模型输出协议失败 | 本地评测异常 |
|---|---|---:|---:|---:|---:|---:|---:|
| officeqa | baseline | 172 | 31 | 137 | 0 | 4 | 0 |
| officeqa | best | 172 | 108 | 36 | 0 | 28 | 0 |
| officeqa | final | 172 | 108 | 36 | 0 | 28 | 0 |
| spreadsheetbench | baseline | 280 | 86 | 191 | 0 | 0 | 3 |
| spreadsheetbench | best | 280 | 217 | 60 | 0 | 0 | 3 |
| spreadsheetbench | final | 280 | 225 | 52 | 0 | 0 | 3 |

OfficeQA final 与 best 技能文件哈希一致，官方训练器复用 best 结果；这不是独立的第三次测试，不应重复统计为额外调用。

## OfficeQA

baseline 4 次、best 28 次异常均为 `Model neither produced a tool request nor a final answer`。逐条末轮消息均非空，但没有 `<answer>` 标签。未发现最终 API 连接失败、HTTP 502/499 或请求超时。

旧报告 `API failures` 列存在误分类：收集器将 `agent_ok=false` 一并视为 API 失败；这些记录实际属于模型输出协议失败。此处仅纠正归因，不修改原始分数。

## SpreadsheetBench

baseline、best、final 各有 3 次异常，均为同样的 3 个题目：`60-7`、`283-32`、`130-9`。对应记录均为 `llm_ok=true`、`code_ok=true`，并且至少一个输出表格已生成。评分/自动校验解析工作表与单元格范围时报错；不是最终 API 调用失败。

- `60-7`: `Consolidated Tracker is not a valid coordinate or range`，多工作表标注解析问题。
- `283-32`: `invalid literal for int() with base 10: ''`，整列 `A:G` 范围没有行号。
- `130-9`: `b2b is not a valid coordinate or range`，工作表名中逗号被错误拆分。

## 证据边界

普通测试未记录逐次 HTTP 响应审计，因此“未发现最终因 API 失败而结束的测试题”不等于“每次请求都从未出现过可重试错误”。现有逐题终止原因没有证据支持把上述异常归因于 API 上游服务。

未修改评测代码、原始结果或分数；未启动模型调用或恢复已暂停补测。逐题分类、源文件行号与 SHA-256 见同目录 `audit.json`。
