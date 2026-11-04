# 其余四个 Benchmark 的环境

环境都安装在 `reproduction/.conda/`，Python 均为 3.11.18。采用两个新环境，
保留已完成 SearchQA / LiveMath 实验的 `max-skillopt` 环境：

| Benchmark | Conda 环境 | 依赖与验证方式 |
|---|---|---|
| DocVQA | `max-skillopt-tools` | Pillow、datasets、pyarrow；文档图片编码、官方多模态消息构建和 ANLS 评分 |
| OfficeQA | `max-skillopt-tools` | 官方默认 offline 模式；本地文档 read / grep 工具 |
| SpreadsheetBench | `max-skillopt-tools` | pandas、openpyxl；实际执行 Python 修改 xlsx，使用官方评测器比较结果 |
| ALFWorld | `max-skillopt-alfworld` | ALFWorld 0.4.2、TextWorld 1.6.2、Gymnasium、OmegaConf、NumPy 1.26.4；官方多进程模拟器 reset / step / close |

前三者使用相同的模型客户端和兼容的图片/数据/表格依赖，适合共用环境。
ALFWorld 包含 TextWorld、Fast Downward 和 Jericho 原生扩展，并独立固定
NumPy 版本，采用隔离环境。按官方 `configs/alfworld/default.yaml` 和
`skillopt/envs/alfworld/vendor/config_tw.yaml` 使用 `AlfredTWEnv` 文本环境；
不需要本地模型、GPU、三维 THOR 或 BUTLER 预训练模型。

## 安装与使用

从仓库根目录运行：

```bash
cd /root/max/SkillOpt
bash reproduction/setup_benchmark_environments.sh all
# 也可以单独安装：tools 或 alfworld
```

脚本使用 Conda 创建环境，再用 uv 安装固定版本的依赖。
`requirements-*.in` 记录直接依赖；`requirements-*.lock.txt` 保存验证后的完整包版本。
ALFWorld 的 Fast Downward / Jericho 原生扩展使用本机 GCC / G++ / Make 工具链构建。
TextWorld 固定为官方依赖范围 `>=1.6.1` 内的 1.6.2，使用对应 Python 3.11 的
Linux wheel，避免 1.7.0 发布包的 WHEEL Tag 元数据问题。

可以直接激活环境：

```bash
conda activate /root/max/SkillOpt/reproduction/.conda/max-skillopt-tools
# 或
conda activate /root/max/SkillOpt/reproduction/.conda/max-skillopt-alfworld
```

推荐用包装脚本执行 Python，它会同时设置 PATH、HF 缓存和 ALFWORLD_DATA，
把新增数据与缓存留在 `reproduction/`：

```bash
bash reproduction/benchmark_python.sh docvqa -m pip check
bash reproduction/benchmark_python.sh officeqa scripts/train.py --help
bash reproduction/benchmark_python.sh spreadsheetbench scripts/eval_only.py --help
bash reproduction/benchmark_python.sh alfworld -m pip check
```

## 功能检查与游戏数据

```bash
bash reproduction/benchmark_python.sh docvqa \
  reproduction/check_benchmark_environments.py --profile tools

# 下载并展开官方 alfworld-download 使用的三个文本游戏数据包与逻辑文件
bash reproduction/benchmark_python.sh alfworld \
  reproduction/prepare_alfworld_environment.py
bash reproduction/benchmark_python.sh alfworld \
  reproduction/check_benchmark_environments.py --profile alfworld
```

ALFWorld 数据位于 `reproduction/data/alfworld/`，来源 URL 和文件 SHA-256
保存在其中的 `provenance.json`。全部 191 个官方划分游戏路径已下载。
功能检查分别抽取 train / val / test 的一个真实游戏，通过 SkillOpt 官方环境
构建器启动多进程 worker，执行重置与 `look` 动作，再关闭。

检查脚本不读取模型 API Key、不调用模型；DocVQA / OfficeQA / SpreadsheetBench
使用临时图片、文档和工作簿验证运行环境。这些检查不代表模型实验成绩。
最新本地报告写入 `reproduction/.cache/environment_check_*.json`。

2026-11-05 已通过上述全部功能检查；两套环境均通过 `pip check`，训练与评测
CLI 的 `--help` 可正常启动。相关官方测试共 46 项通过，12 项跳过（可选
Parallel MCP 搜索未安装，官方默认 offline 模式不依赖它）。验证记录与
ALFWorld 数据来源摘要保存在
[`artifacts/environment_preparation_20261105/`](artifacts/environment_preparation_20261105/)。

## 数据与后续实验的边界

后续已补齐 DocVQA、SpreadsheetBench 的完整数据整理和三项任务的 Terra / High
配置，并完成真实 API 小规模训练流程检查，包括个人网关的图片输入。
ALFWorld 的官方游戏资产也已就绪。完整 Benchmark 训练尚未启动。

OfficeQA 官方数据源受 Hugging Face 授权限制；用户尚无权限，按要求暂缓。
默认 offline 模式的软件与本地工具已准备好。

当前就绪状态、数据来源与校验、真实 API 检查结果、启动命令统一见
[`READINESS.md`](READINESS.md)。完整数据源与划分定义见官方
[`data/README.md`](../data/README.md)。
