# Live Mathematical MCQ Heuristics

## Option Comparison

- If a meta-option says that another choice is correct but a stronger result is provable, check both parts: identify a true listed statement and independently establish a strictly stronger conclusion, even if it is not listed. Do not automatically choose the strongest explicit contender or assume an unsupported strengthening.

- Compare strength by logical implication rather than surface wording: statements involving different norms, exponents, objects, or hypotheses may not be directly comparable.
- Compare all options before committing. The correct choice is often the strongest statement justified by the question, while nearby distractors are weaker, overstrong, or miss an equality case.
- Track exact quantifiers such as "there exists", "for every", "if and only if", and "exactly when".

## Theorem-Level Precision

- For close contenders or strongest-result questions, distinguish what a theorem or proof actually establishes from what merely sounds plausible. Check the evidence for differing implications, endpoints, realization claims, and additional conclusions. Actively consider possible strengthenings, such as sharper estimates or quantitative witnesses, but verify that they hold under the stated hypotheses, imply the candidate, and are genuinely stronger; do not invent a strengthening or converse from the weaker claim.

- Break contenders into atomic claims and verify each against the theorem or proof. Check implication directions, both halves of biconditionals, necessity and sufficiency in equality characterizations, and any asserted converses or obstructions. Compare boundary points and parameter ranges explicitly; do not silently repair an unbound exponent or other apparent typo.

- In estimates, track quantifier order and precisely which quantities each constant may depend on; a constant that depends on an interval does not give a bound uniform over all intervals. Test proposed bounds against simple homogeneous or zero-data cases.
- Check whether an option weakens the conclusion by dropping a characterization, equality clause, or full equivalence.
- Check whether an option overstates the theorem by upgrading regularity, removing scale restrictions, or changing an existential statement into a universal one.

## Hypotheses

- If a counterexample appears to invalidate all choices, check that it satisfies every stated assumption and that the alleged failure concerns the full option, not just a paraphrase. Do not discard a valid objection merely to force a choice; recognize possible flaws in the question.

- For case-by-case statements or explicit classifications, check initial cases and relevant symmetries, and verify precisely which object each conclusion describes.
- Verify the hypotheses and domain carefully. Distractors often keep the theorem shape but alter the required assumptions.
- Pay close attention to equality cases, extremal conditions, and whether a result applies to the full family or only a restricted subfamily.

## Final Answer
- Output the final answer as the single option label only.

<!-- SLOW_UPDATE_START -->
Before selecting a correct explicit option, compare it with every stronger listed option and every meta-option. For a meta-option, identify a true listed statement and verify a strictly stronger result under the same hypotheses; look especially for a stronger witness, a quantitative estimate, or an additional conclusion. Do not treat an unusually strong explicit witness as automatically optimal, and do not invent a strengthening.

When one option adds a structural or probabilistic conclusion to another, check that extra conclusion independently rather than defaulting to the shorter statement. For block-stable graph classes, check both the generating-function threshold and the associated block-tree branching-process regimes, including the critical boundary and any conditioning on size.

For piecewise existence and regularity results, verify each transition value against the actual theorem: inclusion of an endpoint is an additional claim, not a harmless interpolation. Compare exponent attainment separately from the parameter range, and track the exponent used in the function space independently of the symbol appearing in its constraint. If the wording contains a mismatch, do not silently repair it or assume the corrected version establishes the literal claim.

For theorem-specific implication questions, verify each directed implication for the exact class and hypotheses; a known equivalence between two properties does not determine either direction involving a third. In particular, distinguish a proved implication from the converse predicted by a conjecture or known only in a narrower setting. Test proposed counterexamples for specialized maps against the actual construction and quotient.

For local PDE estimates, inspect the entire displayed inequality, including solution norms, energy factors, enlarged domains, and distance-to-initial-time factors. Distinguish a solution-independent constant from a solution-independent bound, and positive-time local regularity from uniform regularity up to initial time. When a Hölder estimate is listed, also check whether the theorem supplies a more informative intrinsic or local quantitative estimate before ruling out a meta-option. Examine all choices before concluding that none works, and do not presume unstated structural assumptions are absent.

For extremal graph estimates, compare equality conditions with the full structure of the extremizer, including its edges, not just component orders; check small parameters and degenerate remainders.
<!-- SLOW_UPDATE_END -->
