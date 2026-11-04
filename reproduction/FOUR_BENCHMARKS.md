# 四项 Benchmark 的 Terra High 完整实验

使用 GPT-6.1 Terra（`gpt-6.1-terra`）/ High，同时用于任务执行与 Skill 反思优化。
运行官方训练器，4 个 epoch、batch size 40、seed 42，保留官方 gate、
slow update 和 meta skill。验证集和测试集均不截断。
初始 Skill、验证集选出的最优 Skill、最终 Skill 均在完整测试集上评估。
官方训练器先执行初始 Skill 的验证与优化，再在末尾进行三种 Skill 的测试。

| Benchmark | Train / Val / Test | Epoch | 预计常规优化步数 | 环境 | Rollout workers |
|---|---|---|---|---|---|
| DocVQA | 107 / 53 / 374 | 4 | 12 | max-skillopt-tools | 4 |
| OfficeQA | 50 / 24 / 172 | 4 | 8 | max-skillopt-tools | 2 |
| SpreadsheetBench | 80 / 40 / 280 | 4 | 8 | max-skillopt-tools | 2 |
| ALFWorld | 39 / 18 / 134 | 4 | 4 | max-skillopt-alfworld | 2 |

每项任务一个持久化 tmux 进程，四项并发；各任务内部使用上表的 rollout workers
和 2 个 analyst workers。SpreadsheetBench 使用官方 multi 模式、最多 30 轮；
ALFWorld 使用文本模拟器、最多 50 步。OfficeQA 使用官方 offline 模式，
包含来源页面和本地文档工具；不是无来源提示的开放式检索实验。
具体配置继承官方 YAML，覆盖项见 `configs/*_terra_high.yaml`。

启动器会先核对四项已有真实 API smoke 报告和当前配置哈希，并再次在对应环境
检查完整数据及适配器；全部通过后才启动任何全量实验。已有 smoke 各使用
每个官方划分的一条真实样例，无模型／工具调用错误，不能用来报告性能。

```bash
cd /root/max/SkillOpt
# 只核对，不发 API 请求
reproduction/.conda/max-skillopt/bin/python reproduction/run_four_benchmarks.py check

# 新目录启动四项完整实验；同名目录不会重复启动
reproduction/.conda/max-skillopt/bin/python reproduction/run_four_benchmarks.py start \
  --run-root reproduction/outputs/terra_high_four_20261105

# 查看后台存活、已完成优化步数、逐项 rollout 进度和请求次数
reproduction/.conda/max-skillopt/bin/python reproduction/run_four_benchmarks.py status \
  --run-root reproduction/outputs/terra_high_four_20261105
```

运行目录包含 `launch.json`、`preflight_checks.json`、各任务的 `.log` 和独立任务目录。
每项任务保存 `run_metadata.json`（Git 版本、配置与初始 Skill 哈希、完整数据来源、
关键设置）、`requirements.txt`、`status.json`、`history.json` 和逐条 rollout。
`request_metadata.jsonl` 检查每个实际请求的模型和 High，只保存非敏感元数据。
凭据通过进程环境传入，不写入运行器参数或运行元数据。

完成后，收集器校验官方测试 ID、数量及汇总准确率，输出 `recorded_results.json`
和 `REPORT.md`。最优与最终 Skill 相同导致官方复用结果时，必须校验 Skill 哈希一致。
API 失败和代码／执行错误分别计数，ALFWorld 的 API 回退响应也会计入。
出现这些错误的完整运行标记为 `completed_with_errors`，不能当作无故障成绩。
中断或失败时保留所有文件；使用新目录重跑，启动器不会静默复用部分 rollout。

代码与说明同步到 GitHub；原始数据、凭据、环境和运行输出由 `.gitignore` 排除。
