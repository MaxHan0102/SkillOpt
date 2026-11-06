# DocVQA：通过显式代理重测剩余超时记录

User-requested explicit-proxy rerun of only the two remaining timeout occurrences; one evaluation per selected task, keep every outcome; same images, skills, Terra/High, seed and timeout; retain all 374 test tasks and preserve previous results; no evolution

仅重测最优 Skill 仍超时的两题。已有成功记录与历史结果保留；结果属于异常补测合并，并非独立全量评测或 avg@5。
显式代理是本轮唯一有意改变的运行设置，分母保持374，再次异常或答错均保留。

| Skill | 本轮题数 | 答对 | 执行异常 | 前次合并 hard | 本次合并 hard | 本次合并 ANLS |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 0 | 0 | 0 | 81.82% | 81.82% | 90.81% |
| best | 2 | 2 | 0 | 92.78% | 93.32% | 96.94% |
| final | 0 | 0 | 0 | 91.98% | 91.98% | 96.02% |
