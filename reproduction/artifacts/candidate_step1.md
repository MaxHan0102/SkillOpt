# Question Answering Skill

## Clue interpretation and evidence matching
- Treat declarative clues such as “He was…” or “In 1884 she…” as implicit requests to identify the person or entity described. Resolve the pronoun from the supplied evidence, not from the most prominent name in the question.
- Match distinctive details—quoted language, work titles, dates, or relationships—to passages that explicitly connect them to an entity. Use corroborating snippets to resolve truncated evidence or incomplete names, and ignore unrelated search-result text.

## Answer precision
- Return only the identified entity's name rather than repeating the clue or adding biography. When the context does not explicitly supply the answer text, use the entity's complete, conventional name. Follow the requested answer format and keep explanatory material outside the answer.

## Preserve explicit answer text
When the context explicitly supplies the answer, copy the minimal answer span faithfully. Preserve its spelling, capitalization, and meaningful symbols or connectors (for example, retain `&` rather than rewriting it as `and`); do not paraphrase an already concise answer.
