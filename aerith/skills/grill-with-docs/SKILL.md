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

The brief has these fields and no others: `summary` (the problem, the intended
outcome, the scope and the non-goals, in plain prose); `users` (each intended user
or operator role by `name` with a `description`; the spec may only use these names,
or `system` for controller invariants); `success_conditions` (at least one
observable condition); `constraints`; `decisions` (a stable `id`, the `decision`
and its `rationale`, only for choices the operator settled or the evidence fixes);
and `glossary` `entries`. Never write a placeholder such as "...": the controller
refuses it. A guess is not a decision: ask it as a question instead. Challenge
terminology against the existing glossary, and preserve decision history rather
than replacing it with a new interpretation.

Read the bundled [engineering method](../../references/engineering-method.md)
for interviewing and domain-modeling details. No other installed skill is needed.

In controller mode, return the requested `brief` and `questions` object; the
controller persists it. In standalone use, deliver the brief and stop. Do not
publish tickets, implement code, edit host rules, or begin another stage merely
because this skill was invoked. Full progression requires `/project-creator`.
