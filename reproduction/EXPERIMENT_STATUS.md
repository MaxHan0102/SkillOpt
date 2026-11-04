# 完整实验启动记录

2026-11-04 15:46:12（Asia/Shanghai）已在宿主机持久 tmux 会话启动两个实验。
本文件记录启动状态；实时状态以本机 `status` 命令和各任务日志为准。

| 项目 | SearchQA | LiveMathematicianBench |
|---|---|---|
| 模型（目标 / 优化） | gpt-6.1-terra | gpt-6.1-terra |
| 请求推理强度 | high | high |
| 官方 train / val / test | 400 / 200 / 1400 | 35 / 17 / 125 |
| 配置 epoch / 预计训练步数 | 4 / 40 | 4 / 4 |
| 启动后已确认阶段 | 初始验证集真实推理 | 初始验证集真实推理 |

两份完整数据已物化并通过官方 ID 覆盖、唯一性、样本数与文件哈希检查。
环境为 `reproduction/.conda/max-skillopt/`（Python 3.11.18）。
离线验证 **59 passed**，新增脚本通过 Ruff，环境安装脚本通过 `bash -n`。
运行请求审计已确认两个任务实际发送 `gpt-6.1-terra` 和 `reasoning_effort=high`。

运行代码提交：`ef03768`。运行器为各任务保存完整代码提交号、配置哈希、
数据来源、Python 版本和依赖清单。数据来源摘要见
[`artifacts/terra_high_data_provenance.json`](artifacts/terra_high_data_provenance.json)。

当前没有最终指标；完成后的结果报告会自动写到各任务目录。

```bash
cd /root/max/SkillOpt
reproduction/.conda/max-skillopt/bin/python reproduction/run_experiments.py status \
  --run-root reproduction/outputs/terra_high_20261104
```

本地完整输出分别在：

- `reproduction/outputs/terra_high_20261104/searchqa/`
- `reproduction/outputs/terra_high_20261104/livemathematicianbench/`

运行、查看日志和恢复说明见 [`FULL_EXPERIMENTS.md`](FULL_EXPERIMENTS.md)。
