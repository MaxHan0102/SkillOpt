# DocVQA 普通实验：超时记录再次补测

User-authorized second attempt for exactly the four saved timeout occurrences; one fresh evaluation each; retain every new outcome; keep all previous successful records; same images, skills, model, reasoning, seed and timeout; no training or evolution

仅重测上轮超时的4条执行，保留已成功的5条；旧结果完整保留。超时仍计0分，分母保持374。
这是按用户要求进行的第二轮异常补测合并，不是独立全量评测，也不是 avg@5。

| Skill | 本轮题数 | 本轮答对 | 本轮执行异常 | 上轮合并 hard | 本轮合并 hard | 本轮合并 ANLS |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 1 | 1 | 0 | 81.55% | 81.82% | 90.81% |
| best | 3 | 1 | 2 | 92.51% | 92.78% | 96.41% |
| final | 0 | 0 | 0 | 91.98% | 91.98% | 96.02% |
