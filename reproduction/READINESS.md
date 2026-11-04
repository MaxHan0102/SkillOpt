# 四个新增 Benchmark 的运行准备

2026-11-05：DocVQA、SpreadsheetBench、ALFWorld、OfficeQA 的环境、完整官方
划分数据和 Terra / High 配置均已就绪，且已通过真实模型 API 的小规模训练流程检查。

| Benchmark | 完整数据（train / val / test） | 配置 | 真实 API 小规模流程 |
|---|---|---|---|
| DocVQA | 107 / 53 / 374，534 张题目关联图片 | 已就绪 | 已完成：执行、反思、候选更新、验证门控、测试 |
| SpreadsheetBench | 80 / 40 / 280，Verified 400 表格资产 | 已就绪 | 已完成：代码生成、实际执行、官方评分、反思、测试 |
| ALFWorld | 39 / 18 / 134，191 个游戏路径 | 已就绪 | 已完成：真实模拟器、动作执行、任务完成、反思、测试 |
| OfficeQA | 50 / 24 / 172，697 份文本和 697 份页面 JSON | 已就绪 | 已完成：来源页读取、文档工具调用、反思、候选更新、验证门控、测试 |

所有模型角色均为 `gpt-6.1-terra` / `reasoning_effort=high`，使用已有个人网关。
认证信息只由进程环境读取，不写入配置。四份配置位于
`reproduction/configs/<benchmark>_terra_high.yaml`，继承官方对应配置。
完整运行保留 4 epoch、完整划分及官方慢更新/meta-skill 设置；并发量适当降低。
ALFWorld 的原始 ID 与 LiveMath 的映射 ID 均可由复现层读取器正确读取反思轨迹。
`tool_reasoning_compat.py` 在复现层补回官方后端在工具调用分支省略的 High 参数；
发出请求前的配置审计仍会执行，不修改官方源文件。

## 下载与检查

从仓库根目录执行，所有新增数据和输出均留在 `reproduction/`。

```bash
cd /root/max/SkillOpt
# ALFWorld 游戏资产（已完成，可在新机器重复执行）
bash reproduction/benchmark_python.sh alfworld reproduction/prepare_alfworld_environment.py
# 四个任务的数据整理与校验；OfficeQA 首次下载需要已获授权的 HF 登录
bash reproduction/benchmark_python.sh docvqa reproduction/prepare_additional_data.py all

# 分别检查完整配置、官方数据加载器和数据完整性；不调用模型 API
for benchmark in docvqa spreadsheetbench alfworld officeqa; do
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

OfficeQA 使用官方 manifest 的固定 revision
`8ecbf18d3833daf4750a903d14963e4c4c1d4cd8`。用户完成授权登录后，下载脚本
`prepare_officeqa_data.py` 显式指定 `https://huggingface.co`，覆盖本次 SDK
请求的端点选择，不更改用户默认 `HF_ENDPOINT` 镜像设置。
脚本使用本地 HF 凭据下载 246 道题目及全部解析文本/页面 JSON，共 1395 个文件，
逐一核对 LFS SHA-256 或 Git blob SHA-1。246 道题目引用的 285 份文档全部存在。
官方 offline 模式使用上述文本与解析页面，不需要 PDF 或第三方搜索服务。

```bash
bash reproduction/benchmark_python.sh officeqa reproduction/prepare_officeqa_data.py
```

原始题目、文档和完整文件哈希清单位于 Git 忽略的 `reproduction/data/`。
HF 登录令牌沿用本地缓存，不打印、不写入实验配置或公开结果。

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
| OfficeQA | 9 | 1 | 读取真实来源页并调用本地文档工具；产生 1 条编辑、验证门控接受；测试样本由错误变为正确 |

四个有效运行的请求均携带指定模型和 High，模型/API/工具执行错误均为 0。
模型回答或工作簿内容不正确时，官方评分器给低分；这与系统执行失败分别记录。
无新增编辑是优化器的有效决定，不会强制伪造更新。这些样本极少，**不能用来
判断完整 Benchmark 准确率或 Skill 优化收益**。

前三项原始输出：`reproduction/outputs/readiness_20261105_v2/<benchmark>/`；
OfficeQA 原始输出：`reproduction/outputs/readiness_20261105_officeqa_v2/officeqa/`。
可公开检查的脱敏报告：

- [`docvqa_smoke.json`](artifacts/readiness_20261105/docvqa_smoke.json)
- [`spreadsheetbench_smoke.json`](artifacts/readiness_20261105/spreadsheetbench_smoke.json)
- [`alfworld_smoke.json`](artifacts/readiness_20261105/alfworld_smoke.json)
- [`officeqa_smoke.json`](artifacts/readiness_20261105/officeqa_smoke.json)

重新运行小规模检查会调用真实模型 API，输出目录须为空：

```bash
bash reproduction/benchmark_python.sh docvqa reproduction/run_benchmark.py smoke docvqa
# 将两个 docvqa 都替换为 spreadsheetbench、alfworld 或 officeqa，可运行对应任务。
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
bash reproduction/benchmark_python.sh officeqa reproduction/run_benchmark.py train officeqa \
  --out-root reproduction/outputs/officeqa_terra_high_full
```

单独评测已有 Skill：

```bash
bash reproduction/benchmark_python.sh docvqa reproduction/run_benchmark.py eval docvqa \
  --skill reproduction/outputs/docvqa_terra_high_full/best_skill.md --split valid_unseen
```

Conda 环境选择、离线依赖检查见 [`BENCHMARK_ENVIRONMENTS.md`](BENCHMARK_ENVIRONMENTS.md)。
