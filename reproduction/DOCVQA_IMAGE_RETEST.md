# DocVQA 超限图片补测

普通实验 `terra_high_four_20261105/docvqa` 的3条失败记录并非一般的临时502：
上游图片处理后的 patch 数超过每张30,000的限制。原图与原实验结果完整保留。

本次使用 `retest_docvqa_images.py`，仅对初始、最优、实际最终 Skill 各补测3题，
共9次执行。模型保持 `gpt-6.1-terra`、High，seed=42，其余评测配置使用原实验快照；
不重新训练或演化。Key 严格从 `reproduction/.env` 加载，实际 HTTP 认证头、
模型、推理强度与上传图片均在发送前核验，不回退到继承的环境 Key。

统一预处理规则：使用32×32像素估算 patch 数；原图超过30,000时，将原始长宽
同时乘以0.85并向下取整；必要时继续乘0.85，直到不超过28,000。
采用 LANCZOS 重采样、RGB PNG，保留整页，不裁剪。相同图片处理结果在所有 Skill 中复用。

| 题目 ID | 原尺寸 | 处理后尺寸 | 原估算 patches | 处理后估算 patches |
|---|---|---|---:|---:|
| 45837 | 5367×7184 | 4561×6106 | 37800 | 27313 |
| 58310 | 6915×5386 | 5877×4578 | 36673 | 26496 |
| 45842 | 5367×7184 | 4561×6106 | 37800 | 27313 |

`manifest.json` 冻结输入、原始结果、Skill、代码和图片的哈希。
每个版本仅补测一次，保留答错及再次异常，不从多次结果中挑选最优答案。
合并结果保留其余371题，分母仍为374；必须标注为“图片处理后补测合并结果”，
并非一次新的全量评测，也不属于此前 avg@5 实验。

本次输出目录：
`reproduction/outputs/docvqa_image_resize_retest_20261106/`。
其中 `evaluations/` 为补测逐题结果及轨迹，`composite/` 为带来源标记的合并结果，
`REPORT.md`、`retest_summary.json` 为新旧分数对照，`status.json` 为进度。
之前暂停的 DocVQA avg@5 补测任务保持暂停。

```bash
cd /root/max/SkillOpt
# 只读核对冻结输入。
bash reproduction/benchmark_python.sh docvqa reproduction/retest_docvqa_images.py check \
  --run-root reproduction/outputs/docvqa_image_resize_retest_20261106
# 根据已完成的补测重新生成独立合并报告。
bash reproduction/benchmark_python.sh docvqa reproduction/retest_docvqa_images.py report \
  --run-root reproduction/outputs/docvqa_image_resize_retest_20261106
```

离线检查：图片缩放上限、原图保留、替换范围与分母、低分保留，以及现有实际请求
与 Key 来源回归检查共27项通过，Ruff 通过。

上游限制参考：[OpenAI Images and vision](https://developers.openai.com/api/docs/guides/images-vision)。

2026-11-06 18:00:50 至18:31:08（Asia/Shanghai）已完成全部9次题目执行。
初始 Skill 补测2题答对、1题达到官方外层660秒任务超时；最优 Skill 的3题均超时；
最终 Skill 的3题全部正常返回且答对。超时均按0分保留，整体状态为 `completed_with_errors`。
因此图片输入限制已被绕过，但本轮并非所有 Skill 都获得无异常评测结果。
官方配置的单次调用超时为600秒，原生适配器任务外层另加60秒，补测保持该规则。

完整374题的初始／最优／最终 hard 分数由81.02%／92.51%／91.18%
变为81.55%／92.51%／91.98%；最终 ANLS 由95.22%变为96.02%。
已核对每个版本的371条未补测记录均未改变、全部374个 ID 覆盖完整、
逐条均值与汇总一致，实际请求审计均通过 `.env` Key、Terra、High 校验。

可分享的分数及9次逐题状态见
[`artifacts/docvqa_image_resize_retest_20261106/REPORT.md`](artifacts/docvqa_image_resize_retest_20261106/REPORT.md)
与同目录 `recorded_results.json`，不含图片、完整轨迹或认证信息。

## 用户要求再次补测超时记录

使用 `retry_docvqa_timeouts.py` 在独立目录
`reproduction/outputs/docvqa_timeout_retry_20261106/` 对初始 Skill 的 `45842`
和最优 Skill 的 `58310`、`45837`、`45842` 各重新执行一次。
上轮已成功的5条记录保留，最终 Skill 的结果保持原样。
旧超时记录、原始374题结果和第一轮图片补测结果均完整保留。

复用原补测入口，模型、High、seed=42、配置、Skill 和缩放图片均保持不变，
实际 HTTP Key 继续严格匹配 `.env`。额外只记录 HTTP 状态码和响应耗时，
不记录认证头或响应体。600秒调用超时／660秒外层任务超时规则不变。
再次超时或正常答错都保留，不额外选择多次中的最高分。
第二轮合并结果单独报告，不代表独立全量复测或 avg@5。

```bash
bash reproduction/benchmark_python.sh docvqa reproduction/retry_docvqa_timeouts.py check \
  --run-root reproduction/outputs/docvqa_timeout_retry_20261106
bash reproduction/benchmark_python.sh docvqa reproduction/retry_docvqa_timeouts.py report \
  --run-root reproduction/outputs/docvqa_timeout_retry_20261106
```

与 Key 来源、图片处理、精确替换、超时筛选及 HTTP 诊断脱敏有关的33项离线检查及 Ruff 均通过。

本轮于2026-11-06 19:29:02启动、19:42:43结束（Asia/Shanghai），4条均完成一次重测。
初始 Skill 的 `45842` 返回 HTTP 200并答对，最优 Skill 的 `58310` 也在重试后答对；
最优 Skill 的 `45837`、`45842` 仍达到660秒任务超时，按0分保留。
初始／最优／最终的完整374题 hard 分数现为81.82%／92.78%／91.98%。
最终 Skill 的全部结果与上轮逐字节一致，其他未选中记录均保持不变。

HTTP 诊断实际记录到网关返回499和504，支持存在服务链路异常的判断；
仅凭这些状态码不能将原因定位到 Key 本身或具体上游环节。
本轮完成状态为 `completed_with_errors`，仍有2条执行异常。
独立报告及脱敏逐题／HTTP 状态记录见
[`artifacts/docvqa_timeout_retry_20261106/REPORT.md`](artifacts/docvqa_timeout_retry_20261106/REPORT.md)
及同目录 `recorded_results.json`。

## 用户要求通过代理补测剩余两题

新增 `retry_docvqa_proxy.py`，仅对上一轮最优 Skill 仍超时的 `45837`、`45842`
各再次执行一次。输出在 `reproduction/outputs/docvqa_proxy_retry_20261106/`。
使用原始配置快照、同一批缩放图片和 Skill，Terra / High、seed=42、600／660秒超时规则不变。
API Key 继续严格从 `.env` 获取并在实际请求发出前核对。

这次按用户要求显式指定服务器的 `https_proxy`（兼容标准大小写变量及 ALL_PROXY），
客户端设置 `trust_env=False`，所以历史入口写入的 NO_PROXY 不会导致绕过代理。
每次发送前检查实际选中的底层连接池必须为 HTTPProxy／SOCKSProxy；没有代理配置或
实际传输为直连时拒绝发送。代理地址和密码不写入分享日志。

`proxy_route.json` 保存代理传输核验结论；`proxy_requests.jsonl` 为带题目 ID 和
本地 attempt_id 的请求时间线；`http_responses.jsonl` 额外保留错误响应正文及可用的
request-id／trace-id 头；`transport_errors.jsonl` 保存代理连接异常。
错误内容中的实际 API Key、代理 URL 和代理密码会脱敏，认证头和 Cookie 不保存。
成功记录与旧实验结果保留，374题的分母不变；这是再次异常补测合并，不是独立全量测试。

```bash
bash reproduction/benchmark_python.sh docvqa reproduction/retry_docvqa_proxy.py check \
  --run-root reproduction/outputs/docvqa_proxy_retry_20261106
bash reproduction/benchmark_python.sh docvqa reproduction/retry_docvqa_proxy.py report \
  --run-root reproduction/outputs/docvqa_proxy_retry_20261106
```

相关37项离线检查及 Ruff 通过，包含实际 SDK 的代理传输选择、NO_PROXY 绕过防护、
直连传输拒绝、HTTP错误正文／request-id记录，以及代理连接错误脱敏。

本轮于2026-11-06 20:26:09–20:26:25（Asia/Shanghai）执行完毕，两题均在首次
HTTP 请求中返回200且答对：`45837` 为6.648秒，`45842` 为15.538秒（至收到响应头）。
实际代理连接池为 HTTPProxy，来源 `https_proxy`，每次发送均核验代理路径；
实际请求的 `.env` Key、Terra、High 审计全部通过。没有 HTTP 错误、重试或任务超时。

完整374题的初始／最优／最终 hard 分数现为81.82%／93.32%／91.98%，
对应 ANLS 为90.81%／96.94%／96.02%。三种合并结果现均无执行异常。
初始与最终 Skill 结果与上一轮逐字节一致，最优 Skill 仅替换两条指定记录。
原始结果和所有历史补测结果保留，这些分数仍须标注为多轮异常补测合并结果。

用户已明确要求通过代理发送请求。后续同类补测应采用显式代理入口并核验传输；
历史直连入口保留供追溯，不应当作当前推荐的运行方式。
完整分数、逐题状态、代理核验和 request-id 见
[`artifacts/docvqa_proxy_retry_20261106/REPORT.md`](artifacts/docvqa_proxy_retry_20261106/REPORT.md)
及同目录 `recorded_results.json`。
