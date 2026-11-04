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
这四项完整实验尚未启动。
详见 [`READINESS.md`](READINESS.md)。
