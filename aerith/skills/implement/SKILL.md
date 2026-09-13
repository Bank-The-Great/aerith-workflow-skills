---
name: implement
description: Implement authorized software tickets within their explicit scope, using tests and independent spec plus defect review before delivery.
---

# Implement

Use the selected vendor's `second-highest` model profile. Work only on a ready
ticket whose blockers are verified. Read the original spec and assigned criteria,
not only the ticket summary. Preserve user edits and operate on the assigned
isolated branch. Never merge, push, deploy or change credentials/host policy.

Use test-first vertical slices at the specified public seams where practical;
see the bundled [engineering method](../../references/engineering-method.md).
Implement the behavior, not a hard-coded fixture answer. Do not skip assertions,
weaken acceptance criteria, bypass failing guards or mark tests passed yourself.

In controller mode return complete UTF-8 file proposals with the expected
pre-edit SHA-256, using only the ticket's exact write set. No tool calls or direct
writes are permitted. The controller applies proposals, runs isolated tests,
collects independent reviews and owns completion/commits. Respond to findings by
fixing their cause; ask when the correct fix needs broader authority or scope.
The first safety release updates existing tracked files only; creating, deleting,
renaming, linking or changing file modes requires a future separately reviewed
capability rather than a null pre-edit hash.
Each review attempt must be controller-owned and bound to the exact code, spec,
ticket, test evidence and scope. A failed review requires a new attempt identity
after the fix. The final integrated review after all tickets must be a separate
fresh pass and cannot reuse a prior ticket review or failed review.

Code Review means both spec-review and the independent defect-review role. Same
vendor is the default, never the same author context. A passed spec review alone
does not close a ticket. Standalone use implements only the requested scope;
it does not invent a new project or trigger unrelated planning/publication.
