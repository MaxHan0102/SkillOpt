# 四个新增 Benchmark 的运行准备

2026-11-05：DocVQA、SpreadsheetBench、ALFWorld 的环境、完整官方划分数据和
Terra / High 配置均已就绪，且已通过真实模型 API 的小规模训练流程检查。
OfficeQA 因官方数据访问授权未取得，按用户要求暂缓。

| Benchmark | 完整数据（train / val / test） | 配置 | 真实 API 小规模流程 |
|---|---|---|---|
| DocVQA | 107 / 53 / 374，534 张题目关联图片 | 已就绪 | 已完成：执行、反思、候选更新、验证门控、测试 |
| SpreadsheetBench | 80 / 40 / 280，Verified 400 表格资产 | 已就绪 | 已完成：代码生成、实际执行、官方评分、反思、测试 |
| ALFWorld | 39 / 18 / 134，191 个游戏路径 | 已就绪 | 已完成：真实模拟器、动作执行、任务完成、反思、测试 |
| OfficeQA | 尚未下载；官方端点返回 401 | 已就绪 | 未执行，等待数据权限 |

所有模型角色均为 `gpt-6.1-terra` / `reasoning_effort=high`，使用已有个人网关。
认证信息只由进程环境读取，不写入配置。四份配置位于
`reproduction/configs/<benchmark>_terra_high.yaml`，继承官方对应配置。
完整运行保留 4 epoch、完整划分及官方慢更新/meta-skill 设置；并发量适当降低。
ALFWorld 的原始 ID 与 LiveMath 的映射 ID 均可由复现层读取器正确读取反思轨迹。

## 下载与检查

从仓库根目录执行，所有新增数据和输出均留在 `reproduction/`。

```bash
cd /root/max/SkillOpt
# ALFWorld 游戏资产（已完成，可在新机器重复执行）
bash reproduction/benchmark_python.sh alfworld reproduction/prepare_alfworld_environment.py
# DocVQA / SpreadsheetBench 数据整理，以及 ALFWorld 路径检查
bash reproduction/benchmark_python.sh docvqa reproduction/prepare_additional_data.py all

# 三个任务分别检查完整配置、官方数据加载器和数据完整性；不调用 API
for benchmark in docvqa spreadsheetbench alfworld; do
  bash reproduction/benchmark_python.sh "$benchmark" \
    reproduction/run_benchmark.py check "$benchmark"
done
```

DocVQA 与 SpreadsheetBench 使用 SkillOpt 官方 manifest 固定的数据版本，
原始下载 SHA-256 与 Hugging Face LFS 元数据一致。题目按照官方 ID 顺序整理，
使用官方数据加载器核对 ID、数量与字段，并核对全部提取图片/工作簿的文件哈希。
DocVQA 实际加载需要 CSV；SpreadsheetBench 与 ALFWorld 使用 JSON 划分。
具体来源、划分和本地完整 provenance 的摘要见
[`artifacts/readiness_20261105/data_provenance.json`](artifacts/readiness_20261105/data_provenance.json)。

SpreadsheetBench 原始 Verified 400 包有一处文件名笔误：42930 号任务的答案
命名为 `1_43930_golden.xlsx`。准备脚本保留原文件，添加内容和哈希相同的
`1_42930_golden.xlsx`，让官方案例读取器识别该题；此变动记录在 provenance
的 `filename_aliases` 中，没有更改答案单元格或评分器。

## 小规模流程检查的实际范围

每个任务使用官方 train / val / test 各自的第一题（保持划分不交叉），运行
一个 epoch / 一个训练步骤，再评测初始、验证集选出的最优、最终技能。
ALFWorld 限制为每题 12 个动作，SpreadsheetBench 限制最多 3 轮；
这些限制仅用于 smoke，不影响完整运行配置。单 epoch 的 smoke 关闭慢更新与
meta-skill，完整运行仍开启这两项。

| Benchmark | 审计请求 | 实际 analyst 调用 | 行为与结果 |
|---|---:|---:|---|
| DocVQA | 6 | 1 | 图像请求成功；产生 1 条编辑、构建候选，验证门控拒绝该候选；测试样本回答正确 |
| SpreadsheetBench | 6 | 1 | 训练样本修改结果正确；验证/测试样本仍有答案不匹配；代码均成功执行，反思决定不新增编辑 |
| ALFWorld | 19 | 1 | 训练、验证、测试样本任务均完成；反思决定不新增编辑 |

三个运行的请求均携带指定模型和 High，模型/API/工具执行错误均为 0。
模型回答或工作簿内容不正确时，官方评分器给低分；这与系统执行失败分别记录。
无新增编辑是优化器的有效决定，不会强制伪造更新。这些样本极少，**不能用来
判断完整 Benchmark 准确率或 Skill 优化收益**。

原始输出：`reproduction/outputs/readiness_20261105_v2/<benchmark>/`。
可公开检查的脱敏报告：

- [`docvqa_smoke.json`](artifacts/readiness_20261105/docvqa_smoke.json)
- [`spreadsheetbench_smoke.json`](artifacts/readiness_20261105/spreadsheetbench_smoke.json)
- [`alfworld_smoke.json`](artifacts/readiness_20261105/alfworld_smoke.json)

重新运行小规模检查会调用真实模型 API，输出目录须为空：

```bash
bash reproduction/benchmark_python.sh docvqa reproduction/run_benchmark.py smoke docvqa
# 将两个 docvqa 都替换为 spreadsheetbench 或 alfworld，可运行对应任务。
```

## 完整运行

完整 Benchmark 训练尚未启动。以下命令使用完整数据和官方优化流程，在前台运行：

```bash
bash reproduction/benchmark_python.sh docvqa reproduction/run_benchmark.py train docvqa \
  --out-root reproduction/outputs/docvqa_terra_high_full
bash reproduction/benchmark_python.sh spreadsheetbench reproduction/run_benchmark.py train spreadsheetbench \
  --out-root reproduction/outputs/spreadsheetbench_terra_high_full
bash reproduction/benchmark_python.sh alfworld reproduction/run_benchmark.py train alfworld \
  --out-root reproduction/outputs/alfworld_terra_high_full
```

单独评测已有 Skill：

```bash
bash reproduction/benchmark_python.sh docvqa reproduction/run_benchmark.py eval docvqa \
  --skill reproduction/outputs/docvqa_terra_high_full/best_skill.md --split valid_unseen
```

Conda 环境选择、离线依赖检查见 [`BENCHMARK_ENVIRONMENTS.md`](BENCHMARK_ENVIRONMENTS.md)。
