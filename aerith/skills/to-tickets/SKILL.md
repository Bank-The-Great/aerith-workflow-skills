---
name: to-tickets
description: Split a settled software specification into verifiable vertical slices with explicit dependencies and complete acceptance-criterion coverage.
---

# To Tickets

Use the selected vendor's `highest` model profile. Read the full supplied spec,
not just a summary or a tracker title. Keep the spec as the acceptance authority.

Create one independently verifiable vertical slice per ticket. Record a stable
ID, title, criterion IDs, blocker IDs and an exact write set within the operator's
approved scope. Each criterion has exactly one primary ticket owner. Ensure all
criteria are covered, every blocker exists, and the graph has no cycle. Explain
genuine blockers rather than mechanically making every task depend on the last.

For broad mechanical refactors, use expand, migrate, contract phases, preserving
compatibility until the final verification. Read the bundled
[engineering method](../../references/engineering-method.md).

Do not ask for routine stage approval when the brief is settled. Ask only about
material ambiguity, missing authority or an unavoidable scope change. Return
`tickets` and `questions` in controller mode. The controller creates one local
artifact per ticket and manages optional tracker mirrors. In standalone mode,
produce tickets and stop; never start a worker or treat a tracker label as consent.
