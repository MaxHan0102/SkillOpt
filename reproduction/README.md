# SkillOpt 简易复现

GPT-6.1 Terra / High 的两个完整官方 Benchmark 实验见
[`FULL_EXPERIMENTS.md`](FULL_EXPERIMENTS.md)。以下保留先前的小样本运行说明。

官方源码：<https://github.com/microsoft/SkillOpt>

个人 fork：<https://github.com/MaxHan0102/SkillOpt>

源码起点：`bc0a392ef0a7f92840ec673d80204d34ced169d0`（SkillOpt 0.2.0）。

这是一次真实模型 API 的流程复现，验证 rollout → reflect → aggregate →
select → update → evaluate。
它使用小样本和自选模型，**不代表复现论文中的性能指标**。
原有优化器、提示词、评分实现不作修改。

## 目录结构

我们新增的脚本、配置、文档、结果，以及本机的网关配置、数据和完整输出，
统一放在 `reproduction/` 下：

```text
reproduction/
├── run.sh                    # 启动脚本
├── prepare_data.py           # 小样本数据准备
├── configs/searchqa_smoke.yaml
├── .env.example              # 网关配置模板
├── .env                      # 本机配置，Git 忽略
├── data/                     # 本机数据，Git 忽略
├── outputs/                  # 完整运行输出，Git 忽略
├── data_manifest.json        # 数据 ID 与哈希
├── artifacts/                # 可分享的结果与技能
├── README.md
└── RESULTS.md
```

配置通过 `_base_` 引用官方配置。仓库根目录 `.venv/` 是安装 SkillOpt 的
共享 Python 环境，不提交到 Git。

## 环境与运行

在仓库根目录执行（需要 Python 3.10+ 和 uv，无需 GPU）：

```bash
uv venv .venv --python python3
uv pip install --python .venv/bin/python -e '.[dev]'
cp reproduction/.env.example reproduction/.env
# 在 reproduction/.env 中填写 OPENAI_COMPATIBLE_BASE_URL 和可用的 OPENAI_COMPATIBLE_MODEL。
# 在终端设置 OPENAI_COMPATIBLE_API_KEY；也兼容现有 MAXAPI_API_KEY。
bash reproduction/run.sh
```

当前机器已配置 `reproduction/.env`，密钥从已有 `MAXAPI_API_KEY` 读取。
`.env`、虚拟环境、原始数据和完整运行输出均由 Git 忽略。
公开文件不包含个人网关地址或密钥。

脚本默认使用 `gpt-6.1-sol`，可用 `OPENAI_COMPATIBLE_MODEL` 更换。
使用官方 `openai_compatible` 后端调用 `/chat/completions`；
本次使用的网关已实际验证支持该路径。
网关模型列表不保证每个模型都能调用，运行前应确认模型实际可用。

## 数据和配置

`prepare_data.py` 从 Hugging Face Dataset Viewer 获取真实
`lucadiliello/searchqa` 数据，不需要下载整个数据集。
固定取原始 `validation` 的前 20 行，依次划为 train 8 / val 4 / test 8，
无 ID 重叠。该划分用于低成本流程验证，**不同于论文发布的 400/200/1400 划分**。
`data_manifest.json` 记录数据来源、每个 split 的 ID 和 SHA-256；
后续准备数据时会校验源数据是否变化。

准备的数据写入 `reproduction/data/searchqa_smoke/`。
配置见 `reproduction/configs/searchqa_smoke.yaml`：1 epoch，batch size 4，
2 次训练更新，edit budget 从 2 调度到 1，2 个并发 worker。
保留严格提升才接受的验证门控，并给慢更新启用验证门控。
慢更新和 meta skill 开关保持开启；按官方实现，第一个 epoch 的慢更新仅插入
空占位，meta skill 跳过，因此本次 1 epoch 运行未实际验证跨 epoch 更新。
训练结束由官方训练器分别用初始技能和最优技能评测同一测试集。

默认写入一个全新的 `reproduction/outputs/searchqa_smoke_时间戳/`。
也可指定目录（相对路径以仓库根目录为基准）：

```bash
bash reproduction/run.sh reproduction/outputs/my_run
```

目录已存在时脚本会退出，避免旧缓存混入新实验。
主要产物为 `best_skill.md`、`history.json`、`steps/`、
`test_eval_baseline/results.jsonl`、`test_eval/results.jsonl` 和 `console.log`。
本次运行的结果和可分享产物见本目录的 `RESULTS.md` 与 `artifacts/`。

## 验证与 GitHub 同步

```bash
.venv/bin/pytest -q tests/test_openai_compatible_backend.py \
  tests/test_openai_compatible_config.py tests/test_gate.py \
  tests/test_scoring.py tests/test_materialize_searchqa.py
.venv/bin/ruff check reproduction/prepare_data.py
bash -n reproduction/run.sh
```

`origin` 指向 `MaxHan0102/SkillOpt`，`upstream` 指向 `microsoft/SkillOpt`，
默认推送到 `origin`。后续同步官方更新：

```bash
git fetch upstream
git merge upstream/main
git push origin main
```

## SpreadsheetBench 异常题目单独补测

`retry_spreadsheet_errors.py` 从已完成的稳定性实验中，固定选择
`item_fingerprints.jsonl` 中 `execution_ok=false` 的 `(轮次, 技能版本, 题目)`。
普通答错的题目不重测。每次异常执行只补测一次，使用原冻结配置、技能、
模型、seed 和评分实现；补测仍失败也保留，不取两次结果中的较高分。

```bash
bash reproduction/benchmark_python.sh spreadsheetbench reproduction/retry_spreadsheet_errors.py prepare \
  --source-root reproduction/outputs/stability_avg5_20261105 \
  --run-root reproduction/outputs/spreadsheet_error_retry_20261106
bash reproduction/benchmark_python.sh spreadsheetbench reproduction/retry_spreadsheet_errors.py start \
  --run-root reproduction/outputs/spreadsheet_error_retry_20261106
bash reproduction/benchmark_python.sh spreadsheetbench reproduction/retry_spreadsheet_errors.py status \
  --run-root reproduction/outputs/spreadsheet_error_retry_20261106
```

新目录保存固定异常清单、输入和源文件哈希、实际请求参数、HTTP 响应状态、
逐题轨迹及原始分数副本。仅在补测子进程中为已有 API 主机追加
`NO_PROXY/no_proxy`，并检查 HTTP 客户端直连，绕过异常本地代理。
各原始轮次/技能组依次执行，组内并发沿用原配置。

全部补测完成后，`composite/` 和 `rerun_summary.json` 将固定选中的异常
执行替换为各自一次补测结果，并保留逐项来源。`REPORT.md` 对照展示
原始 avg@5 和补测合并均值。合并结果使用原始记录和补测记录，
不能视为重新完成五轮全量实验；原始报告始终保留。
脚本拒绝重复启动或重用已开始的补测目录，避免意外多次抽样。

DocVQA 和 OfficeQA 使用同一补测协议，通过独立入口启动，例如：

```bash
bash reproduction/benchmark_python.sh docvqa reproduction/retry_qa_errors.py prepare \
  --benchmark docvqa --source-root reproduction/outputs/stability_avg5_20261105 \
  --run-root reproduction/outputs/docvqa_error_retry_20261106
bash reproduction/benchmark_python.sh docvqa reproduction/retry_qa_errors.py check \
  --benchmark docvqa --run-root reproduction/outputs/docvqa_error_retry_20261106
bash reproduction/benchmark_python.sh docvqa reproduction/retry_qa_errors.py start \
  --benchmark docvqa --run-root reproduction/outputs/docvqa_error_retry_20261106
```

OfficeQA 将以上命令中的 `docvqa` 换成 `officeqa`。`status` 和 `report`
动作可查询进度和生成报告。API 连接、502、超时和模型未形成有效输出
分别统计；后者也反映模型原始表现，不能当作纯网络问题。
报告同时保留 hard 分数和 DocVQA ANLS / OfficeQA F1 的原始值与补测合并值。

用户要求重新启动 DocVQA 补测时，使用 `restart_docvqa_retry.py` 创建新的
补测目录，并通过 `prepare --source-root 原稳定性实验目录 --previous-root 上次补测目录
--run-root 新补测目录` 固定原异常清单。旧目录的结果、日志和失败尝试全部保留，
不按两次成绩高低选择结果。`check/start/status/report` 接受同一 `--run-root`。
该入口仅在独立 trial 子进程完成原评分、保存指标与终态后直接退出，取消超时后
仍阻塞的底层 API 线程，避免 Python 退出时等待线程而拖住后续评测。
