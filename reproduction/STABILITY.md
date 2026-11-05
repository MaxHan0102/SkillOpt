# 六项 Benchmark 的 avg@5 与轨迹稳定性测试

目的：在同一道题、相同设置下反复执行，检查成功／失败是否一致，以及回答和执行轨迹
是否波动。只评测官方 test / valid_unseen，不训练、不反思、不修改 Skill。

本次已于 **2026-11-05 12:12:52（Asia/Shanghai）** 启动六项并发全量测试，
输出根目录为 `reproduction/outputs/stability_avg5_20261105/`，运行代码 `3e070ad`。
18 次真实 API 启动检查均完成且无最终调用／执行错误，已确认六个后台会话存活并发出请求。
启动摘要见 [`launch_summary.json`](artifacts/stability_avg5_20261105/launch_summary.json)。

每项任务测试三个固定版本，各执行五次完整测试：

- `none`：零字节 Skill 文件，保留官方基础任务提示词和工具。
- `initial`：此前完整实验保存的 `skills/skill_v0000.md`，核对官方初始 Skill 哈希。
- `final`：此前实验 `runtime_state.json` 的 `current_skill_path`，即实际训练末尾版本。
  数学任务使用路径兼容修复后的独立重跑版本。

| Benchmark | 测试集题数 | 每版本次数 | 总题目执行数 | 环境 | 内部 workers |
|---|---:|---:|---:|---|---:|
| SearchQA | 1400 | 5 | 21000 | max-skillopt | 4 |
| LiveMathematicianBench | 125 | 5 | 1875 | max-skillopt | 4 |
| DocVQA | 374 | 5 | 5610 | max-skillopt-tools | 4 |
| OfficeQA | 172 | 5 | 2580 | max-skillopt-tools | 2 |
| SpreadsheetBench | 280 | 5 | 4200 | max-skillopt-tools | 2 |
| ALFWorld | 134 | 5 | 2010 | max-skillopt-alfworld | 2 |

合计 90 次完整评测、37,275 次题目执行，另有每项／每版本一题的 18 次启动检查。
每个 Benchmark 一个后台协调进程，串行执行本任务的 15 次评测；六项并行。
每次评测是新 Python 进程和新目录，禁止复用此前结果或其他重复的缓存。

## 相同设置

任务模型仍为 `gpt-6.1-terra` / High。每次均固定 seed=42，保留此前实验的
轮数／动作预算、工具、数据、超时和内部并发。固定 seed 保持数学选项排列及
ALFWorld 初始化／分块种子一致；没有新增 API seed 或 temperature 覆盖。
模型服务仍可能产生随机输出，这正是本次要观察的现象。
OfficeQA 保持官方 offline 来源页面和本地工具模式；SpreadsheetBench 保持官方
multi 模式及其原有执行反馈。不会为了本次稳定性测试改变工具行为。

启动前冻结配置、三个 Skill、来源路径和哈希，并检查与此前实验设置一致。
六项依赖与完整数据检查通过后，再并发执行 18 次真实 API 启动检查；全部通过才
开启完整评测。启动检查的答错或生成代码失败是正常任务结果；API／适配器异常会阻止启动。
启动及全量评测均通过实际请求审计强制 Terra / High，并禁止创建 optimizer 客户端。

五轮按 `none→initial→final`、`initial→final→none`、`final→none→initial` 循环轮换
版本顺序，以分散运行时段影响。每轮使用相同设置，重复编号不用于修改随机种子。

## 统计

`avg@5` 是五次完整测试准确率的算术平均，同时报告 soft 分和五次 hard 分的样本标准差。
每题按五次成功次数 0–5 计数，并区分始终成功、始终失败、成功／失败混合。
报告成对结果不一致率：若一题成功 s 次，则其不同结果的配对数为 s(5−s)，
除以五次评测的 10 个配对。

调用、输出格式、生成代码和执行异常保留原始记录，并保留在准确率分母中；
另外统计五次都无执行错误的题目，避免将异常导致的波动直接归因于任务推理。
ALFWorld 即使官方标记 agent_ok，也会检查 API 错误、空响应、缺失 action 的回退轨迹。
不会自动补跑并替换失败。若某次评测不完整，保留异常并继续其余评测；缺少完整五次的
版本不输出 avg@5，整体标为 incomplete。

每题保存答案、完整会话、行为和初始提示的哈希，以及实际轮数。哈希比较仅去除
本次输出目录、随机 tool-call ID 和响应时间／usage 元数据；保留回答、代码、动作和反馈。
完整会话变化可能只是措辞变化，因此同时保留行为和答案的比较。
ALFWorld 比较动作序列及完整轨迹，其官方日志不保存初始提示，所以提示哈希为空。
缺失轨迹不视为相同轨迹，单独记录。

## 运行与查看

```bash
cd /root/max/SkillOpt
reproduction/.conda/max-skillopt/bin/python reproduction/run_stability.py start \
  --run-root reproduction/outputs/stability_avg5_20261105

reproduction/.conda/max-skillopt/bin/python reproduction/run_stability.py status \
  --run-root reproduction/outputs/stability_avg5_20261105
```

六个 tmux 会话在终端退出后继续运行。输出位于指定根目录：

- `inputs/<benchmark>/`：冻结配置、三个 Skill、来源和哈希。
- `preflight.json`、`smoke_checks.json`、`launch.json`：资源、数据、启动检查与并发记录。
- `<benchmark>/evaluations/repeat_01..05/<variant>/`：每次逐题结果、完整轨迹、请求审计、指标。
- `<benchmark>/stability_summary.json`：已完成次数；版本完成五次后增加 avg@5 与波动统计。
- `<benchmark>/{none,initial,final}_stability.jsonl`：同一题五次的结果和轨迹哈希。
- `<benchmark>/REPORT.md`：全部 15 次评测完成后的汇总。
- `<benchmark>/worker.log`、`repeat_*_<variant>.log`：调度进度和各次评测日志。

原始输出、数据、环境和凭据不提交 Git。运行器代码与说明同步至 fork。
