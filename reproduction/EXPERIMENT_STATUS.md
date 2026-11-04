# 实验进度与已核对结果

2026-11-04 检查：首批两个进程均已结束，使用 GPT-6.1 Terra / High。
SearchQA 已完整完成；数学任务发现上游路径兼容问题，需要修复后独立重跑。

| 项目 | SearchQA | LiveMathematicianBench 原运行 |
|---|---|---|
| 官方 train / val / test | 400 / 200 / 1400 | 35 / 17 / 125 |
| Epoch / 训练步数 | 4 / 40 | 4 / 4 |
| 完成时间（Asia/Shanghai） | 18:43:32 | 16:23:09 |
| 初始技能测试准确率 | 75.86% | 57.60% |
| 最优技能测试准确率 | 83.93%（+8.07 个百分点） | 71.20%（受限结果） |
| 最终技能测试准确率 | 84.43% | 71.20%（复用最优技能结果） |
| 测试集 API 失败记录 | 0 | 0 |

SearchQA 验证集准确率由 70% 提升至 82%，40 步中 3 次接受、33 次拒绝、
4 次因优化模型判断无须新增编辑而跳过。3 次 slow update 和 3 次 meta skill
更新均有实际模型调用。完整测试集 1400 条，结果均值已与逐条记录核对。

**数学原运行不能视为完整优化流程的复现结果。** 其 ID 含冒号，例如
`202602:22`，rollout 使用官方安全映射写入 `_mapped-<sha256>` 目录，
但上游反思格式化器仍查找原 ID 目录，因此 4 个常规步骤没有实际 analyst 调用。
原运行保留了慢更新与 meta skill，71.2% 测试成绩主要由这些环节产生。
旧记录保留，并标为 `completed_with_reflection_issue`。

修复位于 `trajectory_compat.py`，只适配文件查找路径，分析提示词保留原始 ID，
普通 SearchQA 提示词字节一致。首批 35 条真实数学轨迹已离线验证全部可读，
新增回归测试证明可进入 analyst 调用。更新后相关检查 **61 passed**。
运行器现在也拒绝将没有任何 analyst 调用的实验标为完整流程成功。

数学修复重跑使用独立输出目录：
`reproduction/outputs/terra_high_20261104_reflection_fix/`。
实时进度以 `status` 和任务日志为准。

```bash
cd /root/max/SkillOpt
reproduction/.conda/max-skillopt/bin/python reproduction/run_experiments.py status \
  --run-root reproduction/outputs/terra_high_20261104_reflection_fix
```

SearchQA 结果和技能见
[`artifacts/terra_high_20261104/searchqa/REPORT.md`](artifacts/terra_high_20261104/searchqa/REPORT.md)。
数学旧结果限制见
[`artifacts/terra_high_20261104/livemathematicianbench_original/LIMITATION.md`](artifacts/terra_high_20261104/livemathematicianbench_original/LIMITATION.md)。

原始完整输出仍在 `reproduction/outputs/terra_high_20261104/`。
初次运行代码为 `ef03768`；当前修复后的运行会另行记录代码版本和配置哈希。
环境、网关与恢复说明见 [`FULL_EXPERIMENTS.md`](FULL_EXPERIMENTS.md)。
