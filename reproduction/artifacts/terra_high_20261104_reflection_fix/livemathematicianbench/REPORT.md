# livemathematicianbench: GPT-6.1 Terra / High

| Skill | Test count | EM/accuracy | F1/soft | API failures |
|---|---:|---:|---:|---:|
| baseline | 125 | 0.592000 | 0.592000 | 0 |
| best | 125 | 0.736000 | 0.736000 | 0 |
| final | 125 | 0.688000 | 0.688000 | 0 |

Steps: 4; accepted: 2; rejected: 2.

Official ID splits and optimizer flow; custom model and operational concurrency/timeouts. This is a new experiment, not the paper's original model result.

Completed: 2026-11-04 22:10:06 Asia/Shanghai (started 21:20:14).

This corrected run uses `reproduction/trajectory_compat.py` to resolve mapped
trajectory directories while preserving public task IDs. All four regular
optimization steps executed reflection: 22 analyst calls, 8 merge calls,
2 accepted steps, 2 rejected steps, and no skipped steps. Three slow updates
and three meta-skill updates also executed. All 776 audited request attempts
used `gpt-6.1-terra` with `reasoning_effort=high`.

The best skill was selected on validation at step 2 (17/17); its test accuracy
is 92/125 = 73.6%, compared with baseline 74/125 = 59.2% (+14.4 percentage
points). The final skill after the official epoch-end slow update scored
86/125 = 68.8%. Use `best_skill.md` for the validation-selected result.
Baseline, best and final tests cover the same 125 unique official IDs, with
zero API failures. Successful-call usage totals 775 calls / 1,778,717 tokens.

The earlier run without working regular reflection remains archived separately
under `terra_high_20261104/livemathematicianbench_original/`. Its results are
superseded for full-flow reproduction. Independent stochastic runs have
different baselines; their score difference is not a controlled ablation.
