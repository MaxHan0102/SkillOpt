# SkillOpt + sub2api 简易复现

官方源码：<https://github.com/microsoft/SkillOpt>

个人 fork：<https://github.com/MaxHan0102/SkillOpt>

源码起点：`bc0a392ef0a7f92840ec673d80204d34ced169d0`（SkillOpt 0.2.0）。

这是一次真实模型 API 的流程复现，验证 rollout → reflect → aggregate →
select → update → evaluate。
它使用小样本和自选模型，**不代表复现论文中的性能指标**。
原有优化器、提示词、评分实现不作修改。

## 环境与运行

在仓库根目录执行（需要 Python 3.10+ 和 uv，无需 GPU）：

```bash
uv venv .venv --python python3
uv pip install --python .venv/bin/python -e '.[dev]'
cp reproduction/sub2api/.env.example .env
# 在 .env 中填写 SUB2API_BASE_URL 和可用的 SUB2API_MODEL。
# 在终端设置 SUB2API_API_KEY；也兼容现有 MAXAPI_API_KEY。
bash reproduction/sub2api/run.sh
```

当前机器已配置根目录 `.env`，密钥从已有 `MAXAPI_API_KEY` 读取。
`.env`、虚拟环境、原始数据和完整运行输出均由 Git 忽略。
公开文件不包含个人网关地址或密钥。

脚本默认使用 `gpt-6.1-sol`，可用 `SUB2API_MODEL` 更换。
使用官方 `openai_compatible` 后端调用 `/chat/completions`；
本次 sub2api 网关已实际验证支持该路径。
网关模型列表不保证每个模型都能调用，运行前应确认模型实际可用。

## 数据和配置

`prepare_data.py` 从 Hugging Face Dataset Viewer 获取真实
`lucadiliello/searchqa` 数据，不需要下载整个数据集。
固定取原始 `validation` 的前 20 行，依次划为 train 8 / val 4 / test 8，
无 ID 重叠。该划分用于低成本流程验证，**不同于论文发布的 400/200/1400 划分**。
`data_manifest.json` 记录数据来源、每个 split 的 ID 和 SHA-256；
后续准备数据时会校验源数据是否变化。

配置见 `configs/searchqa/sub2api_smoke.yaml`：1 epoch，batch size 4，
2 次训练更新，edit budget 从 2 调度到 1，2 个并发 worker。
保留严格提升才接受的验证门控，并给慢更新启用验证门控。
慢更新和 meta skill 开关保持开启；按官方实现，第一个 epoch 的慢更新仅插入
空占位，meta skill 跳过，因此本次 1 epoch 运行未实际验证跨 epoch 更新。
训练结束由官方训练器分别用初始技能和最优技能评测同一测试集。

默认写入一个全新的 `outputs/sub2api_smoke_时间戳/`。也可指定目录：

```bash
bash reproduction/sub2api/run.sh outputs/my_sub2api_run
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
.venv/bin/ruff check reproduction/sub2api/prepare_data.py
bash -n reproduction/sub2api/run.sh
```

`origin` 指向 `MaxHan0102/SkillOpt`，`upstream` 指向 `microsoft/SkillOpt`，
默认推送到 `origin`。后续同步官方更新：

```bash
git fetch upstream
git merge upstream/main
git push origin main
```
