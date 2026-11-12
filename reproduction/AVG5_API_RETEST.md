# avg@5 的 API 异常补测（2026-11-07）

用户确认模型输出问题、本地测试和评分异常正常计为答错，只继续处理 API 调用导致的失败。
用户再次明确：**默认使用代理，不绕过代理**。当前入口为 `retry_avg5_api_errors.py`；
历史 `retry_qa_errors.py` / `restart_docvqa_retry.py` 的直连入口和暂停记录仅保留追溯。

| Benchmark | 本轮执行次数 | 范围 | 输出目录 |
|---|---:|---|---|
| DocVQA | 45 | 同样 3 道题 × 3 个 Skill × 5 次重复 | `outputs/docvqa_avg5_api_proxy_20261107/` |
| OfficeQA | 13 | 第 5 次最终 Skill 的 12 条连接失败、1 条请求超时 | `outputs/officeqa_avg5_api_proxy_20261107/` |

DocVQA 原始分类为 44 条 HTTP 502、1 条连接失败，涉及 `45837`、`58310`、`45842`。
图片沿用普通实验已经验证的固定预处理：按 32×32 像素估算 patches，超过 30,000
则将原始长宽同时乘 0.85，必要时重复缩放，直至估算不超过 28,000；保留整页，
使用 LANCZOS/RGB PNG。原图保留，每个 Skill 和重复使用同一份缩图。

OfficeQA 原始 188 条执行异常中的另外 175 条属于模型未形成合规输出，继续保留原始
0 分；不纳入本轮补测。旧的部分补测及结果单独保留，不把这些模型失败的再次抽样
成绩混入本轮结果。模型、High、seed=42、原冻结 Skill、配置、并发和评分器均保持一致。

每个选中的 `(repeat, variant, task)` 在本轮重新执行一次，使用对应 avg@5 Skill，
不复用普通实验模型输出。所有补测结果（包括答错或再次失败）均保留。
新报告将补测记录精确替换到原始对应轮次，未选中记录不变，每轮分母仍为
DocVQA 374／OfficeQA 172；因此结果标注为“补测合并均值”。

每个实际请求都核验 `.env` Key、Terra、High、任务 ID 和实际代理连接池。
客户端显式设置代理及 `trust_env=False`，不受旧 `NO_PROXY` 绕过设置影响；
没有代理或检测到直连时拒绝请求。DocVQA 额外验证发送的图片哈希。
保存逐请求 HTTP 状态、request ID、脱敏错误及代理验证，不保存认证头。

本次通过现有 tmux server 启动两个独立会话：
`max-avg5-api-docvqa-20261107`、`max-avg5-api-officeqa-20261107`。
各输出目录的 `launch.json` 保存实际 socket 和会话名。

```bash
cd /root/max/SkillOpt
bash reproduction/benchmark_python.sh docvqa reproduction/retry_avg5_api_errors.py status \
  --benchmark docvqa --run-root reproduction/outputs/docvqa_avg5_api_proxy_20261107
bash reproduction/benchmark_python.sh officeqa reproduction/retry_avg5_api_errors.py status \
  --benchmark officeqa --run-root reproduction/outputs/officeqa_avg5_api_proxy_20261107
```

`REPORT.md`、`rerun_summary.json` 保存合并结果，`composite/` 保存带来源的逐题记录。
状态为 `completed` 表示所选记录完成且合并结果无剩余 API/超时失败；模型和本地
执行失败仍可存在，并另列为计错记录。`completed_with_api_errors` 表示仍有调用失败。

验证：相关 32 项离线检查通过，覆盖 API-only 选择、保持分母及非选中记录、
实际 SDK 代理传输、Key/模型审计仍被执行、图片缩放、诊断脱敏与后台子进程退出。

## 已完成结果

两项均已于 2026-11-07 00:41:03（Asia/Shanghai）前完成，状态为 `completed`，
合并结果均无剩余 API／超时失败。

| Benchmark | 完成时间 | 本轮结果 | 实际 HTTP 响应 | 合并均值（无／初始／最终 Skill） |
|---|---|---|---|---|
| DocVQA | 00:40:43 | 45/45 条完成且全部答对 | 45 次，全部 200 | 65.24% / 81.71% / 92.19% |
| OfficeQA | 00:41:03 | 13/13 条完成，7 条答对、6 条答错 | 38 次，全部 200 | 14.65% / 17.91% / 64.30% |

OfficeQA 本轮 6 条答错中包含 2 条模型输出协议失败，依用户口径计为 0 分。
原有 175 条模型输出失败全部保留，因此合并记录中共有 177 条非 API 执行失败，
不再对这些记录补测。所有 83 次实际请求均核验为 HTTPProxy、`.env` Key、Terra/High。

已逐条核对 30 组完整测试的唯一 ID、固定分母及合并均值：DocVQA 仅替换 45 条，
其余 5,565 条未变；OfficeQA 仅替换 13 条，其余 2,567 条未变。
原始结果文件哈希保持一致，DocVQA 缩图哈希与普通实验补测使用的图片一致。

完整报告：
- [DocVQA](outputs/docvqa_avg5_api_proxy_20261107/REPORT.md)
- [OfficeQA](outputs/officeqa_avg5_api_proxy_20261107/REPORT.md)
