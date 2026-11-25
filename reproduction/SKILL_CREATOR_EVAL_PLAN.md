# 在 SkillOpt 上评测 Codex 直接生成的 Skill

ALFWorld 脚本用 `gpt-6.1-terra / high` 采集 39 条空 Skill 训练轨迹，再让独立的 Codex `gpt-6.1-sol / high` 会话调用系统 `$skill-creator` 编写一份英文手册。冻结后，Terra 在同一批 134 道测试题上分别使用空 Skill 和手册正文。脚本直接调用 SkillOpt 原生 ALFWorld 模拟器、动作提示和评分逻辑，整个运行只需要 target 接口和 Codex 的模型服务。[运行脚本](/root/max/SkillOpt/reproduction/run_alfworld_skill_creator.py) · [Codex 写作会话](/root/max/SkillOpt/reproduction/skill_creator_author.py)

## 直接运行

```bash
cd /root/max/SkillOpt
bash reproduction/benchmark_python.sh alfworld reproduction/run_alfworld_skill_creator.py
```

默认测试每组跑 5 遍，共 1,340 次测试 episode。以下参数可加在命令末尾。

| 参数 | 作用 |
|---|---|
| `--repeats 1` | 每组 134 次测试，共 268 次。训练仍是 39 条，编写一份手册 |
| `--validation` | 冻结手册后，两组在 18 道验证题上各跑一遍，再进入测试 |
| `--check` | 检查本机训练与评测配置、清单、游戏文件、target 凭据配置及 Codex 命令，打印计划，无模型调用 |
| `--smoke` | 用一题训练并生成手册，再在一题验证题上跑两组，写入独立目录 |
| `--run-dir PATH` | 指定输出目录。用原命令恢复已完成的批次和冻结手册，更换配置时使用新的目录 |

正式输出在 `reproduction/outputs/alfworld_skill_creator/`。其中 `training_input/trajectories.jsonl` 是提供给作者的训练轨迹，`frozen/SKILL.md` 是生成原件，`frozen/body.md` 是真正注入的正文，`summary.json` 含两组成功率、逐题逐轮分数、配对差值和 token 用量。逐步输入输出在各批次的 `predictions/<id>/conversation.json`。API 重试耗尽会停止当前批次，重新运行时只补未提交的批次。

启动器使用本机已准备的 ALFWorld 环境。Terra 沿用 SkillOpt 的 OpenAI 兼容端点及凭据，Codex 使用启动脚本时的用户配置和模型服务。运行作者会话时，宿主机上的 Codex 状态目录需要可写。[本机环境说明](/root/max/SkillOpt/reproduction/README.md)

## 首轮 ALFWorld

| 配置项 | 建议值 |
|---|---|
| 数据 | SkillOpt 作者发布的 `data/alfworld_path_split`，39 train / 18 val / 134 test |
| 采集器和评测执行器 | `gpt-6.1-terra`，`reasoning_effort=high`，使用相同的 target 请求配置 |
| 环境 | 文本模拟器，`seed=42`，`max_steps=50`，`max_completion_tokens=16384`，使用原生动作和历史提示 |
| 并发 | 对齐历史运行时，训练采集取 `workers=2`、`max_api_workers=2`。两组测试均取 `8/8`，对应本地已有的五轮评测配置 |
| 生成 | 39 个训练任务各用空 Skill 执行 1 次，共采集 39 条轨迹。一个独立 Codex 会话读取全部成功和失败轨迹，产出 1 份手册 |
| 评测 | 冻结后，`none` 和 `creator` 各跑 5 遍完整 test，主指标为环境成功率的 avg@5。18 道 val 可各跑 1 遍作诊断 |

本轮按独立训练题计采集量，39 道题各贡献一条实际执行轨迹。本地原生 SkillOpt 曾在相同的 39 道题上执行 276 次，其中常规训练 156 次，额外采样 120 次。这个数可供以后比较训练开销时使用。[常规批次记录](/root/max/SkillOpt/reproduction/outputs/terra_high_four_20261105/alfworld/steps/step_0001/rollout/results.jsonl) · [额外采样及两次执行的实现](/root/max/SkillOpt/skillopt/engine/trainer.py:1842)

我按相对于 `json_2.1.1` 的完整 gamefile 路径核对了两套清单。SkillOpt 的 39 道训练题只有 5 道出现在 EmbodiSkill 上次的 500 道里，18 道验证题全部包含在上次的 140 道里。134 道测试题完全相同，排列顺序不同，跨框架逐题比较时应按 gamefile 对齐。SkillOpt 每步提供当前可选动作、最近两步的观察与动作，模型通过 `<action>…</action>` 提交动作。上次 EmbodiSkill 的 Qwen 使用 30 步和整段历史，无 Skill 与直接生成 Skill 在测试上分别为 39/134 和 87/134，每个条件执行了一遍。[上次实验](/root/max/EmbodiSkill/reproduction/SKILL_CREATOR_EVAL.md) · [编写模型与结果审计](/root/max/EmbodiSkill/reproduction/outputs/skill_creator_comparison_audit_20261113/REPORT_ZH.md) · [上次的任务清单](/root/max/EmbodiSkill/reproduction/outputs/qwen3vl8b_skill_creator_eval_20261112_205426/manifest.json) · [SkillOpt 动作提示](/root/max/SkillOpt/skillopt/envs/alfworld/prompts/rollout_with_history.md)

分块的起始索引会进入环境种子。采集时按官方 train 清单顺序执行一遍，使用原生分块逻辑和基础种子 42。两组测试共用同一份冻结配置、gamefile 顺序及分块方式，采样参数沿用原请求设置。[实际使用的测试配置](/root/max/SkillOpt/reproduction/outputs/stability_avg5_alfworld_restart_20261106/inputs/alfworld/config.json) · [分块实现](/root/max/SkillOpt/skillopt/envs/alfworld/adapter.py:391)

训练采集沿用基础 YAML 的并发，测试使用已有五轮评测的 8 个环境并发和 8 个 API 并发。每组每轮单独保存结果。[基础 YAML](/root/max/SkillOpt/reproduction/configs/alfworld_terra_high.yaml)

## 采集、编写与注入

1. **采集。** 用原生 `run_alfworld_batch` 和空字符串按顺序跑完整份训练清单。每条保存任务 ID、实际输入、原始回复、动作、环境反馈、终局和原生分数。核对 39 个唯一 ID 及实际游戏路径，每个 ID 恰有一条入选轨迹。[ALFWorld 执行器](/root/max/SkillOpt/skillopt/envs/alfworld/rollout.py)
2. **编写。** 给独立 Codex 会话提供动作协议、公开提示、训练轨迹和原生终局评分。作者可在生成目录写本地脚本分析轨迹。收据记录模型、推理强度、系统 skill-creator 哈希、会话与回合 ID、token 用量和耗时，另存固定编写提示与事件。
3. **冻结。** 用系统 `quick_validate.py` 检查 `SKILL.md` 格式，核对作者申报的训练 ID 和输入哈希。保存原件，剥离 YAML frontmatter 及首尾空白后另存正文，各自记录 SHA-256。验证和测试时直接将正文传给原生执行器。[ALFWorld 注入逻辑](/root/max/SkillOpt/skillopt/envs/alfworld/rollout.py)

建议给生成会话的指令如下，每个 benchmark 只替换任务名和执行器信息。

> 使用系统 `$skill-creator`，根据本目录中的任务协议及全部训练轨迹，为执行器写一份自包含的英文 `SKILL.md`。覆盖成功和失败轨迹，用可观察的反馈核对建议。可以在本地统计或回查原始步骤。只把执行器能用现有动作或工具实施、能迁移到新题的指导写入正文。另存读过的训练 ID、统计依据和生成记录，供实验审计。

## 同一个执行器的两组配对评测

| 组别 | 注入的材料 | 主要用途 |
|---|---|---|
| `none` | 空字符串，执行器使用原生基础提示 | 给出同期无 Skill 基线 |
| `creator` | 本次用全量 train 轨迹直接生成的正文 | 检验直接编写方案 |

两组使用同一配置、原生评分器、相同题序和种子，每轮交替先跑哪一组。验证使用 `valid_seen`，测试使用 `valid_unseen`，结果分别写入 `val/repeat_XX/{none,creator}/` 和 `test/repeat_XX/{none,creator}/`。

主要报告 `creator − none` 成功率分差（百分点）。每道题先对各轮 hard 分取平均，再在题目上取平均。95% 区间按题目 ID 配对重采样，保留每题的全部执行。逐题原生结果含 hard 和 soft。汇总记录任务类别分差、执行步数、输出格式触发的 `look` 回退、正常失败原因和 token。接口报错重试耗尽会中止批次，已完成的游戏失败仍计入分母。训练、写作和评测分别记账。

## 扩到本地已复现的六项任务

| Benchmark | Train / Val / Test 题数 | 历史原生训练轨迹数（参考） | 原生设置 | 测试 workers |
|---|---:|---:|---|---:|
| ALFWorld | 39 / 18 / 134 | 276 | 50 步，最近两步历史 | 8 |
| SpreadsheetBench | 80 / 40 / 280 | 440 | `multi`，最多 30 轮，按测试用例计 hard/soft | 2 |
| LiveMathematicianBench | 35 / 17 / 125 | 260 | 1 轮，沿用选项排列 | 4 |
| OfficeQA | 50 / 24 / 172 | 320 | `offline`，使用原生来源页及本地工具，最多 24 轮工具交互 | 2 |
| DocVQA | 107 / 53 / 374 | 548 | 1 轮，按原生图像管线评测，报告 hard 和 ANLS | 4 |
| SearchQA | 400 / 200 / 1400 | 1,720 | 1 轮，报告 EM 和 F1 | 4 |

ALFWorld 主实验采集 39 条训练轨迹。两组各在相同的 134 道测试题上执行一遍，共 268 次。沿用五轮 avg@5 设置时，共 1,340 次测试。若增加完整验证，再加 36 次。

若将每道训练题执行一次的流程扩至六项，合计采集 711 条空 Skill 轨迹。六项共有 2,485 道测试题，两组各跑五遍共 24,850 次。可选的 352 道验证题各组跑一遍，合计 704 次。这里的单位是整道题执行，多步环境每题可能发出多次模型请求。[六项数据清单](/root/max/SkillOpt/data/README.md:27) · [既有评测协议](/root/max/SkillOpt/reproduction/STABILITY.md:31)

### 另外五项的一键运行

`run_benchmark_skill_creator.py` 覆盖上表中其余五项。每项在独立目录采集原生空 Skill 轨迹，由同一套 Codex App Server 作者和冻结程序生成该任务的手册。作者只读取训练包。LiveMath 按原始题目 ID 对应映射后的轨迹目录，OfficeQA 保留来源页、本地工具和工具调用的 high 参数，SpreadsheetBench 保留原生 multi 模式及执行反馈。DocVQA 仅对超限图片使用既有缩放规则，将执行器实际看到的训练图片一并交给作者。SearchQA 保留原生片段截断和 EM/F1 评分。[通用入口](/root/max/SkillOpt/reproduction/run_benchmark_skill_creator.py) · [作者与冻结逻辑](/root/max/SkillOpt/reproduction/skill_creator_author.py)

```bash
cd /root/max/SkillOpt
bench=livemathematicianbench
bash reproduction/benchmark_python.sh "$bench" reproduction/run_benchmark_skill_creator.py "$bench" --workers 2 --check
bash reproduction/benchmark_python.sh "$bench" reproduction/run_benchmark_skill_creator.py "$bench" --workers 2 --smoke
bash reproduction/benchmark_python.sh "$bench" reproduction/run_benchmark_skill_creator.py "$bench" --workers 2 --repeats 5
```

在第二个进程把 `bench` 换为 `officeqa` 即可同时运行。默认每项使用独立的正式目录和 smoke 目录。`--run-dir` 可指定目录，用相同参数恢复中断的作业。`status.json` 显示当前阶段和已完成题数，`training_input/READY.json` 列出训练 ID 和文件哈希，`frozen/manifest.json` 保存作者收据和正文哈希。`summary.json` 在两组全部完成后给出逐题配对的 hard、soft、五轮均值及区间。普通答错、格式错误和原生评分失败按实际得分保留。未完成的请求保存技术重试记录。

11 月 23 日并发实验的 `smoke/` 和 `full/` 分别保存单题验证和用完整训练集重新编写的正式实验。两项各使用 2 个 worker，正式作业的两组各执行 5 次完整 test。[本次运行目录](/root/max/SkillOpt/reproduction/outputs/skill_creator_parallel_20261123)

同日追加的三项正式运行也在该目录的 `full/` 下。SpreadsheetBench 使用 2 个 worker，并传入 `--isolate-workbooks`，将每个用例的 Python 变换放在无网络的临时文件系统中运行，只提供对应的输入工作簿和 Python 依赖。协调进程取回生成文件，用原生评分器比较。DocVQA、SearchQA 各使用 4 个 worker。三项各从完整训练集重新生成手册，测试仍为两组各五轮。[表格执行入口](/root/max/SkillOpt/reproduction/spreadsheet_isolation.py)

## 按研究问题追加的对照

- **专门测 skill-creator 的贡献。** 在同一训练包上用相同 Codex 模型、工具和编写预算，再生成一份普通 Codex 手册。两边都从头编写，普通组的会话不加载 skill-creator。两份手册交给同一个 target 配对评测。
- **比较现成手册的部署效果。** 如果还关心原生 SkillOpt 的成绩，可以在同一测试协议下直接载入它已经保存的 `final.md`，需要时再加入 `initial.md`。已有手册可直接评分。原生 final 使用在线更新后的轨迹及验证反馈，离线作者只看到空 Skill 轨迹，两边可按训练 episode 数比较，并另记 token 用量。[冻结材料来源](/root/max/SkillOpt/reproduction/run_stability.py:61)
- **专门对比上次 Qwen 的表现。** 可在 SkillOpt 的 ALFWorld 接口上增设 `Qwen/Qwen3-VL-8B-Instruct` 执行器。用它自己的空 Skill 训练轨迹生成手册，再在那 134 道共同测试题上配对跑空 Skill 和新手册。两套框架各自的增益可并列展示，同时报告训练规模、历史窗口、动作协议及重复次数。

## 人工交接如何自动化

11 月 12 日 19:02:56（北京时间），EmbodiSkill 的采集器在审计完成后写出 `READY.json`。正式聊天 **Create Qwen ALFWorld skill** 只有一个用户回合。用户在训练包目录提交了一段英文 prompt，并显式附上了系统 skill-creator。20:29:41 至 20:33:49，Codex 完成了 22 次模型响应、31 条本地命令，分析十批各 50 条轨迹并写出手册。20:57:37 创建了后续评测。采集器的 `run()` 在审计后写完成状态并返回，人工交接发生在训练包就绪和独立写作会话启动之间。[采集完成逻辑](/root/max/EmbodiSkill/reproduction/collect_qwen_train500.py:476) · [生成会话审计](/root/max/EmbodiSkill/reproduction/outputs/skill_creator_comparison_audit_20261113/REPORT_ZH.md:82)

SkillOpt 的运行脚本在训练包写入 `READY.json` 后自动启动作者。作者只收到清单点名的三份文件。文件系统权限允许它读取工作目录、系统 skill-creator 和必要运行时，向 `generated/` 写入产物。协调程序校验首份合格产物并原子发布到 `frozen/`，随后开始配对评测。若 Codex 回合完成却缺少有效文件，程序保存失败原因，并可自动重试一次。[自动写作和冻结](/root/max/SkillOpt/reproduction/run_alfworld_skill_creator.py)

**按原桌面操作显式选择 Skill。** 启动本地 `codex app-server`，通过 stdio 完成 `initialize` 和 `initialized`。协调程序先对作者工作目录调用 `skills/list`，再用该目录和 `gpt-6.1-sol` 调用 `thread/start`。以下为 `turn/start` 的请求形状。运行时填入返回的会话 ID、展开后的完整 prompt 和发现的 Skill 路径，监听 `turn/completed` 并检查最终状态。这样可以保留原消息中的文本和显式 Skill 两项输入。[官方 App Server 协议](https://developers.openai.com/codex/app-server/)

```json
{
  "id": 4,
  "method": "turn/start",
  "params": {
    "threadId": "<thread/start 返回的 ID>",
    "effort": "high",
    "input": [
      {"type": "text", "text": "$skill-creator <本轮完整写作提示词>"},
      {"type": "skill", "name": "skill-creator", "path": "/root/.codex-max/skills/.system/skill-creator/SKILL.md"}
    ]
  }
}
```

较短的实现也可以从外层程序独立启动 `codex exec --json`，从 stdin 读固定 prompt，指定编写模型、推理强度及工作目录，然后从输出目录取正文并保留事件日志。[Codex 非交互模式](https://developers.openai.com/codex/noninteractive/)

**会影响复现的实现细节**

- 历史采集包内现已存在 `generated/alfworld-qwen/SKILL.md` 及分析笔记。以旧包验证自动交接时，从原始 `READY.json` 点名的文件复制输入，创建空的输出目录。新运行把只读训练包与可写产物分开，给生成进程提供训练材料、系统 Skill 及必要工具。工作目录控制起始位置，严格的训练集读隔离需在工作进程的文件系统视图上实现。[采集包生成](/root/max/EmbodiSkill/reproduction/collect_qwen_train500.py:431)
- EmbodiSkill 的旧评测器写死了上次的基线目录、候选名及 SHA-256。若也要重跑 EmbodiSkill 全链路，给新作业传入本轮冻结清单，并保留对旧运行的历史哈希校验。SkillOpt 的评测入口已直接接受文件路径。[历史评测器](/root/max/EmbodiSkill/reproduction/run_skill_creator_eval.py:29) · [SkillOpt 文件入口](/root/max/SkillOpt/scripts/eval_only.py:609)
- ALFWorld 适配器看到已有的非空 `results.jsonl` 就会直接返回读到的记录。每个阶段按实验配置、候选哈希和任务 ID 核对完成标记，并采用独立输出目录。如果写作阶段断线，先核对已保存的会话状态和产物，成功提交的候选只冻结一次。普通任务失败保留在结果中。[评测缓存逻辑](/root/max/SkillOpt/skillopt/envs/alfworld/adapter.py:329)

**2026-11-23 实跑核验。** 本机 Codex CLI `0.169.1` 和真实模型完成了 `--smoke`。Terra 完成一题训练，Sol 通过系统 skill-creator 生成手册，同一题验证上空 Skill 和冻结正文均为 1/1。核查了两组实际请求中的正文注入，并重复运行同一命令验证了断点复用。这一轮覆盖三次 episode，完整 134 题的效应量留给正式实验。[小规模结果](/root/max/SkillOpt/reproduction/outputs/alfworld_skill_creator_smoke/summary.json) · [小规模生成的手册](/root/max/SkillOpt/reproduction/outputs/alfworld_skill_creator_smoke/frozen/SKILL.md)
