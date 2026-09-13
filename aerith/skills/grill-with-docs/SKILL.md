---
name: grill-with-docs
description: Clarify a software project using repository evidence, a focused interview, a glossary, and recorded decisions. Does not implement the project.
---

# Grill with Docs

Use the `highest` model profile of the selected vendor. If the current session
cannot select that profile, hand the stage to the configured controller; never
pretend an unchanged session switched models.

Inspect the supplied repository evidence before asking factual questions. Ask
only decisions whose prerequisites are settled. Give a practical recommendation
and explain its tradeoff. Keep unresolved decisions explicit; do not invent the
operator's preferences. Do not repeatedly ask questions already answered.

Record the problem, intended users, success conditions, scope, non-goals,
constraints, glossary and material decisions with rationale. Separate documented
facts from assumptions. Challenge terminology against the existing glossary.
Preserve decision history rather than replacing it with a new interpretation.

Read the bundled [engineering method](../../references/engineering-method.md)
for interviewing and domain-modeling details. No other installed skill is needed.

In controller mode, return the requested `brief` and `questions` object; the
controller persists it. In standalone use, deliver the brief and stop. Do not
publish tickets, implement code, edit host rules, or begin another stage merely
because this skill was invoked. Full progression requires `/project-creator`.
