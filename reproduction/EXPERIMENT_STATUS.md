# 实验进度与已核对结果

截至 2026-11-05：两个 Benchmark 均已完成，使用 GPT-6.1 Terra / High。
SearchQA 首次完整运行成功；数学任务修复上游路径兼容问题后，已独立重跑完成。

| 项目 | SearchQA | LiveMathematicianBench 修复重跑 |
|---|---|---|
| 官方 train / val / test | 400 / 200 / 1400 | 35 / 17 / 125 |
| Epoch / 训练步数 | 4 / 40 | 4 / 4 |
| 完成时间（2026-11-04，Asia/Shanghai） | 18:43:32 | 22:10:06 |
| 初始技能测试准确率 | 75.86%（1062/1400） | 59.20%（74/125） |
| 验证集选出的最优技能测试准确率 | 83.93%（1175/1400，+8.07 个百分点） | 73.60%（92/125，+14.40 个百分点） |
| 最终技能测试准确率 | 84.43%（1182/1400） | 68.80%（86/125） |
| 测试集 API 失败记录 | 0 | 0 |

SearchQA 验证集准确率由 70% 提升至 82%，40 步中 3 次接受、33 次拒绝、
4 次因优化模型判断无须新增编辑而跳过。3 次 slow update 和 3 次 meta skill
更新均有实际模型调用。完整测试集 1400 条，结果均值已与逐条记录核对。

数学修复重跑从 2026-11-04 21:20:14 至 22:10:06，完成 4 个 epoch / 4 步，
2 次接受、2 次拒绝、0 次跳过。实际执行 22 次 analyst、8 次 merge、
3 次 slow update 和 3 次 meta skill 调用。776 次请求尝试均使用指定模型与 High；
成功调用 775 次，总用量 1,778,717 tokens。
验证集由 11/17 提升至 17/17，最优技能来自第 2 步。
初始、最优、最终测试均覆盖相同的 125 个唯一官方 ID，逐条计分已核对。
最终技能经历官方 epoch 末慢更新后，测试成绩低于最优技能；使用结果时应选
`best_skill.md`，它由验证集选择。这里是替换模型后的单次复现，不代表论文原模型指标。

**数学原运行不能视为完整优化流程的复现结果。** 其 ID 含冒号，例如
`202602:22`，rollout 使用官方安全映射写入 `_mapped-<sha256>` 目录，
但上游反思格式化器仍查找原 ID 目录，因此 4 个常规步骤没有实际 analyst 调用。
原运行保留了慢更新与 meta skill，71.2% 是该受限流程的测试成绩。
旧记录保留，并标为 `completed_with_reflection_issue`。
新旧运行基线不同且模型生成具有随机性，二者差异不能当作受控消融实验。

修复位于 `trajectory_compat.py`，只适配文件查找路径，分析提示词保留原始 ID，
普通 SearchQA 提示词字节一致。首批 35 条真实数学轨迹已离线验证全部可读，
新增回归测试证明可进入 analyst 调用。更新后相关检查 **61 passed**。
运行器现在也拒绝将没有任何 analyst 调用的实验标为完整流程成功。

数学修复重跑使用独立输出目录：
`reproduction/outputs/terra_high_20261104_reflection_fix/`。
状态已为 `completed`；可用以下命令查询保留记录。

```bash
cd /root/max/SkillOpt
reproduction/.conda/max-skillopt/bin/python reproduction/run_experiments.py status \
  --run-root reproduction/outputs/terra_high_20261104_reflection_fix
```

SearchQA 结果和技能见
[`artifacts/terra_high_20261104/searchqa/REPORT.md`](artifacts/terra_high_20261104/searchqa/REPORT.md)。
数学修复重跑结果、最优/最终技能与逐题计分见
[`artifacts/terra_high_20261104_reflection_fix/livemathematicianbench/REPORT.md`](artifacts/terra_high_20261104_reflection_fix/livemathematicianbench/REPORT.md)。
数学旧结果限制见
[`artifacts/terra_high_20261104/livemathematicianbench_original/LIMITATION.md`](artifacts/terra_high_20261104/livemathematicianbench_original/LIMITATION.md)。

原始完整输出仍在 `reproduction/outputs/terra_high_20261104/`。
初次运行代码为 `ef03768`；修复重跑的代码版本和配置哈希保存在本地运行元数据中。
环境、网关与恢复说明见 [`FULL_EXPERIMENTS.md`](FULL_EXPERIMENTS.md)。

2026-11-05：另外四项任务的环境和配置已准备；DocVQA、SpreadsheetBench、
ALFWorld、OfficeQA 的完整官方划分数据与真实 API 小规模训练流程检查已完成。
OfficeQA 在用户取得授权后使用官方端点下载，保留默认 HF 镜像设置。
这四项完整实验于 **2026-11-05 02:00（Asia/Shanghai）** 启动，四个独立 tmux
进程并发运行，代码版本 `e9b0e95`，模型和优化器均为 Terra / High。
与前两项一致，使用完整官方划分、4 个 epoch、官方反思优化流程，
启用 slow update 和 meta skill，最终评测初始／最优／最终三种 Skill。
预计常规优化步数：DocVQA 12、OfficeQA 8、SpreadsheetBench 8、ALFWorld 4。
四项现已全部结束，最后的 SpreadsheetBench 于 10:11 完成。
初始／最优／最终 Skill 完整测试准确率分别为：DocVQA 81.02% / 92.51% / 91.18%，
OfficeQA 18.02% / 62.79% / 62.79%，SpreadsheetBench 30.71% / 77.50% / 80.36%，
ALFWorld 91.04% / 91.79% / 93.28%。
DocVQA 三种测试各有 3 条上游 502 错误；OfficeQA 初始／最优分别有 4 / 28 条
无有效输出，最终与最优相同并复用结果；SpreadsheetBench 三种测试各有 3 条代码执行失败。
这三项为 `completed_with_errors`；ALFWorld 测试无调用错误，最优仍为初始 Skill，
最优与初始分差包含生成随机性。所有失败均保留在计分分母中，尚未补跑替换。
启动前再次核对四项环境、数据和真实 API smoke 报告；相关回归检查 **75 passed**。

```bash
cd /root/max/SkillOpt
reproduction/.conda/max-skillopt/bin/python reproduction/run_four_benchmarks.py status \
  --run-root reproduction/outputs/terra_high_four_20261105
```

四项原始输出与日志均在 `reproduction/outputs/terra_high_four_20261105/`。
详细设置和启动方式见 [`FOUR_BENCHMARKS.md`](FOUR_BENCHMARKS.md)，
数据与小规模验证说明见 [`READINESS.md`](READINESS.md)。

2026-11-05 12:12:52：六项 Benchmark 的独立稳定性测试已并发启动。
固定 Terra / High、seed=42，只评测完整测试集；无 Skill／初始 Skill／实际最终 Skill
各重新执行 5 次，共 90 次全量评测、37,275 次题目执行，不进行演化。
18 次真实 API 启动检查均通过，六个后台会话存活并产生实际请求。
本次新增统计检查后相关测试 **81 passed**。
运行与轨迹波动统计见 [`STABILITY.md`](STABILITY.md)，查询命令如下：

```bash
cd /root/max/SkillOpt
reproduction/.conda/max-skillopt/bin/python reproduction/run_stability.py status \
  --run-root reproduction/outputs/stability_avg5_20261105
```

2026-11-06 01:26:19：额度恢复后已启动三项后台任务，所有新请求强制使用
`reproduction/.env` 中的 Key，并核验实际 HTTP 认证头；不会回退到旧环境 Key。
SearchQA、数学、DocVQA 已各完成 15/15 轮，保留现有结果；OfficeQA 从 10/15
续跑剩余 5 轮，SpreadsheetBench 从 5/15 续跑剩余 10 轮。
ALFWorld 在 `reproduction/outputs/stability_avg5_alfworld_restart_20261106/`
从头执行 15 轮，环境与 API workers 均由 2 提高为 8，三个版本五次重复使用同一并发设置。
启动检查确认三个后台会话存活、请求均为 Terra / High、认证来源为 `.env`，
ALFWorld 日志确认实际采用 8-worker 分块。上述统一 status 命令已指向新的后台会话。
