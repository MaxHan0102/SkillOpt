# DocVQA 普通实验：超限图片处理后补测

One fresh evaluation per original failed (skill, task); keep every outcome, no best-of selection; retain all 374 tasks; no training, evolution, or changes to previous results

仅替换每个 Skill 原始失败的3题，其他371题原样保留；属于补测合并结果，并非重新全量评测。
原图与原始结果不修改；图片预处理方式改变，补测分数单独报告。

| Skill | 原始 hard | 补测3题正确数 | 合并 hard（374题） | 原始 ANLS | 合并 ANLS | 补测执行异常 |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 81.02% | 2/3 | 81.55% | 90.01% | 90.54% | 1 |
| best | 92.51% | 0/3 | 92.51% | 96.14% | 96.14% | 3 |
| final | 91.18% | 3/3 | 91.98% | 95.22% | 96.02% | 0 |
