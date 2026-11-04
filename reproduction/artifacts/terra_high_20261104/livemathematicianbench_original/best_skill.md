# Live Mathematical MCQ Heuristics

## Option Comparison
- Compare all options before committing. The correct choice is often the strongest statement justified by the question, while nearby distractors are weaker, overstrong, or miss an equality case.
- Track exact quantifiers such as "there exists", "for every", "if and only if", and "exactly when".

## Theorem-Level Precision
- Check whether an option weakens the conclusion by dropping a characterization, equality clause, or full equivalence.
- Check whether an option overstates the theorem by upgrading regularity, removing scale restrictions, or changing an existential statement into a universal one.

## Hypotheses
- Verify the hypotheses and domain carefully. Distractors often keep the theorem shape but alter the required assumptions.
- Pay close attention to equality cases, extremal conditions, and whether a result applies to the full family or only a restricted subfamily.

## Final Answer
- Output the final answer as the single option label only.

<!-- SLOW_UPDATE_START -->
When a stronger-result meta-option is present, do not stop after verifying a substantive choice. Identify a specific proved strengthening of a remaining option, including its precise hypotheses, and select the meta-option if both are supported. Check the full theorem for stronger existence or counting conclusions, equivalences, exact identities, additional regularity, and quantitative estimates; a weaker consequence can be correct without being the strongest answer. Do not infer a strengthening merely from the meta-option's presence.

For transitive group actions, check whether the derangement conclusion holds in a smaller subgroup or stronger graph-theoretic form. For equivalence criteria, check whether a fixed ample bundle or weaker numerical test suffices. For arithmetic existence results, check the full simultaneous conditions and count, not just existence or a partial proportion. For PDE regularity, look for the full local or intrinsic estimate, including weighted behavior, scale restrictions, and constant dependencies, rather than stopping at qualitative Hölder regularity.

For piecewise results, make a separate claim for each displayed range and endpoint. An omitted threshold is not an asserted case; adding it requires independent justification. Read each exponent and inequality literally, distinguishing a regularity exponent from a different quantity appearing in its constraint, even when the notation looks unintended.

For implication questions, list the directions actually established for the specified construction; verify each reverse direction separately before asserting equivalence. Use general theorems and counterexamples only after checking that their hypotheses and setting match the question.
<!-- SLOW_UPDATE_END -->
