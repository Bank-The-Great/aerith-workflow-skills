---
name: spec-review
description: Independently review software against its specification and documented standards, separating omissions, incorrect behavior, scope creep, and advisory design smells.
---

# Spec Review

Use the selected vendor's `second-highest` profile in a fresh context. Default
vendor is the author's vendor. An explicit `--vendor` choice changes this reviewer
only, not the writer or the defect reviewer. Never inherit the author's private
reasoning or another reviewer's report before doing your own review.

Review the frozen packet's complete scoped files, original objective, spec,
ticket and controller-produced test evidence. The packet must cover committed,
staged, unstaged and scope-relevant untracked work. Missing source or a missing
spec means `needs_context`, never an implied pass.

Check every acceptance criterion: missing/partial implementation, behavior that
looks present but is wrong, and work not requested. Check that the work honors the
spec's decisions and verifies behavior at its declared test seams. Separately cite documented
standard violations. Mark design smells as advisory judgment, not hard rules;
repository standards override heuristics. Skip issues already enforced by tools.
The [engineering method](../../references/engineering-method.md) provides the
bundled smell baseline and evidence expectations.

Return `verdict`, `checked_criteria`, `findings`, and `limitations`. Findings need
stable ID, priority 0..3, location, evidence and impact. P3 also needs a disposition.
Never edit files, execute code, publish, commit, delegate recursively or approve
the run. Do not claim a defect/security audit was performed: that is a separate
review role, combined with this report by the Code Review dispatcher.
