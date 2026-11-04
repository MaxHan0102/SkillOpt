# GPT-6.1 Terra / High：两个官方 Benchmark 的完整流程

所有新增文件仍集中在 `reproduction/`。目标模型与优化模型均为
`gpt-6.1-terra`，每次模型请求明确发送 `reasoning_effort=high`。

| Benchmark | 训练 | 验证 | 测试 | Epoch / batch |
|---|---:|---:|---:|---|
| SearchQA | 400 | 200 | 1400 | 4 / 40 |
| LiveMathematicianBench | 35 | 17 | 125 | 4 / 40（实际每 epoch 35 条） |

使用官方发布的全部 ID 和固定顺序。SearchQA 原始数据固定到 HF revision
`c1a979068ba118d85467179b704031d113d689cc`，两个 Parquet 的 SHA-256
与官方 HF LFS 元数据一致。LiveMath 按 SkillOpt 清单固定到
`b72450f6ce96c26158d64d945a5d31ef7727be41`，使用 202511—202602
四个月文件及官方归一化函数，保留选项打乱，不向目标模型提供 theorem/sketch。

流程包括完整数据准备、初始技能验证、4 epoch rollout/reflect/aggregate/select/
update/evaluate、严格验证门控、跨 epoch slow/meta update，最后由官方训练器
分别评测初始、最优和最终技能的完整测试集。未启用样本截断或额外推理工具。
这是换用指定模型的新实验，不是论文原模型数值的复测。

逐步修改使用严格验证门控；慢更新沿用官方默认
`slow_update_gate_with_selection=false`，因此跨 epoch 慢更新本身不受该门控约束。
最终技能与最优技能相同时，官方训练器复用最优技能的测试结果，运行器会校验
两份技能字节相同并在汇总中标注复用。

## 环境与脚本

独立 Conda 环境名为 `max-skillopt`，安装在
`reproduction/.conda/max-skillopt/`，Python 3.11.18。
环境、缓存、数据、原始输出均由 Git 忽略；没有改变系统 Python 或其他 Conda 环境。

```bash
cd /root/max/SkillOpt
bash reproduction/setup_environment.sh
conda activate /root/max/SkillOpt/reproduction/.conda/max-skillopt
```

`environment.yml` 描述环境，`requirements-terra-high.txt` 固定本次 Python 包版本。
运行器也会为每个实验保存自己的 `requirements.txt`。

脚本职责：

- `prepare_official_data.py`：下载固定版本数据，按官方 ID 清单生成 split，
  验证唯一性、ID 覆盖、样本数与 SHA-256，保存 `provenance.json`。
- `run_experiments.py`：连通性验证、后台启动/恢复、进度查询、结果聚合。
- `configs/*_terra_high.yaml`：继承对应官方配置，仅指定模型、推理强度、
  数据位置，以及每任务 4 个 rollout worker、2 个 analyst worker、600 秒超时。
- `test_full_pipeline.py`：离线验证两个模型角色的实际 HTTP 请求携带 High，
  请求审计不含提示词或密钥，并检查完整划分与训练开关。
- `trajectory_compat.py`：修复上游反思读取器与安全目录命名的兼容问题。
  LiveMath 的 `202602:22` 等 ID 会被 rollout 映射为 `_mapped-<sha256>` 目录，
  复现层按同一映射读取轨迹，并在分析提示词中保留原始 ID。
  普通 SearchQA ID 的分析提示词保持字节一致；不修改官方源文件。

## 网关与 High 推理

网关仍从 `reproduction/.env` 的 `OPENAI_COMPATIBLE_BASE_URL` 读取；密钥来自
`OPENAI_COMPATIBLE_API_KEY`，也兼容已有环境变量 `MAXAPI_API_KEY`。
模型在本实验配置中固定为 `gpt-6.1-terra`，不会被旧的小样本模型变量覆盖。

当前官方通用 `openai_compatible` 后端会丢弃 `reasoning_effort` 参数，
因此使用官方 `openai_chat` 后端的 `azure_openai_auth_mode=openai_compatible`。
虽然配置属于 Azure 命名空间，实际用标准 OpenAI 客户端访问个人网关，
不要求 Azure 订阅，也不改变官方源文件。

每个请求在发送前由运行器审计；如果模型或推理强度不符合要求，会阻止发送。
`request_metadata.jsonl` 只记录时间、API 路径、模型名、推理强度和输出 token 上限，
不保存认证头、网关域名、密钥、提示词或请求正文。该记录证明客户端发送的配置，
不能独立证明网关内部路由和服务端实现。

## 启动与查看

下列命令使用环境内 Python，不依赖当前 shell 是否激活 Conda：

```bash
PY=reproduction/.conda/max-skillopt/bin/python
$PY reproduction/run_experiments.py preflight
$PY reproduction/run_experiments.py start \
  --run-root reproduction/outputs/terra_high_20261104
$PY reproduction/run_experiments.py status \
  --run-root reproduction/outputs/terra_high_20261104
```

`start` 在专用 tmux server 中为两个实验创建独立会话，命令返回后实验继续运行。
请在可持久运行后台进程的宿主机终端执行；一次性沙箱可能在退出时清理后台进程。
会话名以 `max-skillopt-` 开头，socket 位于 `reproduction/.cache/experiments.sock`。
每任务自身还有文件锁，避免同一输出目录重复运行。

如中断后需要恢复，复用同一目录并加 `--resume`。也可通过
`--benchmark searchqa` 或 `--benchmark livemathematicianbench` 单独启动一个任务。
已有运行中的任务不会重复启动；已完成任务不重跑。

```bash
$PY reproduction/run_experiments.py start --resume \
  --run-root reproduction/outputs/terra_high_20261104
tail -f reproduction/outputs/terra_high_20261104/searchqa/console.log
tail -f reproduction/outputs/terra_high_20261104/livemathematicianbench/console.log
```

## 输出与完成条件

每个任务目录包含 `status.json`、`run_metadata.json`、`console.log`、
`request_metadata.jsonl`、官方 `config.json`/`history.json`/`best_skill.md`、
逐步技能与候选、慢更新和 meta skill，以及三种技能的逐条测试结果。
训练完成后自动核对测试 ID 覆盖与分数均值，写入
`recorded_results.json` 和 `REPORT.md`。

只有训练、评测及结果检查全部返回成功才标为 `completed`。测试调用失败会显示
`completed_with_errors`，进程异常显示 `failed`，会话意外退出显示 `interrupted`。
`preparing_data` 和 `training_and_evaluation` 均表示尚未完成，不能作为复现结果。
模型随机性、API 错误及超时会影响结果；本次数据 seed 保持官方默认 42。
没有任何 analyst 模型调用的运行也会被阻止标为完整流程成功。

2026-11-04 首次数学运行受上述路径兼容问题影响，虽然完成测试，反思环节并未
实际调用优化模型；其 71.2% 只记录为受限旧结果。修复后的数学实验使用独立目录
`reproduction/outputs/terra_high_20261104_reflection_fix/` 重跑，保留原记录。

```bash
$PY -m pytest -q reproduction/test_full_pipeline.py \
  tests/test_azure_openai_compat.py tests/test_gate.py \
  tests/test_materialize_searchqa.py tests/test_holdout_integrity.py
```
