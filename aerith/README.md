# Project Creator: release candidate, not production-admitted

This isolated package adapts five engineering skills from Matt Pocock's MIT
repository. The upstream tree, history, and original notices remain intact.
Source revision: `3cca18b368ae95cdbdebbff572ccafa662551015`.

The public package contains no host credentials, private project artifacts,
host governance documents, or copies of a vendor's proprietary system skills.
`references/defect-review.md` is an independently authored portable role.

## Commands and roles

`grill-with-docs -> to-spec -> to-tickets -> implement -> two-axis review`

The first three stages use the selected vendor's `highest` profile. Implement
and both reviewers use `second-highest`. Model names belong in the host catalog,
not in skills. Reviewer vendor defaults to the author vendor, in fresh contexts.
An explicit spec-review vendor does not change the author or defect reviewer.

Five core skills are independently reusable. `project-creator` is the controller
command, not a sixth planning skill. Code Review means spec compliance plus
bug/security/data-damage review. It is not a guarantee of finding every defect.

Run `python aerith/project-creator.py --help` from the repository root.
Supported commands: start, status, answer, pause, resume, cancel, review, sync,
doctor; explicit maintenance commands: run, recover, catalog-refresh.
`--state` precedes the command. Start/resume/answer run in the foreground unless
`--background` is requested. Help/status/doctor do not start models or workers.
Direct invocation is for development/inspection only. Production and detached
runs require a separately trusted host bootstrap that checks admission and
loads hash-verified package bytes before any package code can execute. The
package does not claim to defend against tampering by verifying itself after
import. No trusted bootstrap means detached execution is refused.

The explicit `review` command only writes review reports to private state. It
does not author code, complete tickets, run tests, or create commits. Full
delivery is available only through the implementation pipeline with actual
verification. A standalone stage does not invoke preceding stages.

## Deterministic boundaries

- Exact file scopes; no wildcard write grants, links, credential paths, or
  shell-profile invocation. Workers return bounded structured data.
- SQLite event chain and full state hashes, revision comparisons, OS-held
  project locks shared across different state roots, edit journals, source
  hashes, and local isolated branches.
- Controller-owned tests, acceptance coverage and dependency checks, P0/P1/P2
  gates, P3 dispositions, and separate ticket/final review packets.
- A failed ticket has a persistent three-attempt ceiling even if code changes.
  Repeated failures pause. A new budget requires an explicit operator decision;
  this candidate does not yet offer a budget-reset command.
- Process cancellation before launch, Windows Job ownership before resume,
  POSIX process-group cleanup, bounded output and deadlines.
- A private GitHub outbox with idempotent markers and read-back. Unknown creates
  are reconciled, never blindly repeated. The initial Issue body is immutable;
  subsequent revisions are idempotent comments to avoid overwriting user edits.
  Local completion and mirror completion are separate statuses.
- Hash-bound package admission with expiry, per-skill revocation, and invocation
  audit. Model and sandbox adapters require retained negative-test evidence.
  Requested models must be independently attested in provider output.

## Host-owned inputs

A run configuration supplies exact `read_set`, `write_set`, approved `tests`
(fixed argv per ID), a private `admission_file`, verified CLI `providers`, and a
verified `verification_sandbox`. Optional `input_artifacts` support standalone
later stages; optional `github` contains a private repository and native CLI
path, never a token. Do not place these host configuration files in this fork.

The model catalog uses `project_creator.vendors.<vendor>` with highest,
second-highest, verified_at, source, and trusted family-discovery expressions.
Catalog freshness is checked at new-run creation. `catalog-refresh` only updates
metadata into an explicit output file; it never installs upstream packages.
Existing run pins do not change on resume. New model families require a ranking
policy decision instead of being ranked by price or version number alone.

## Honest release gates

This candidate is **not installed or enabled in a production host**. Unit tests
inject trusted fixture workers and a trusted synthetic verifier; they are not
proof of live vendor portability or an OS sandbox. No config flag named
`fixture` bypasses production admission.

Still required before release: live Claude/Codex/Gemini conformance, actual
account model availability/attestation, automatic new-run discovery, sandbox
negative probes, native background scheduler registration and login recovery,
GitHub parent/subissue relationship integration and a live mirror test,
canonical per-ticket files, real single-task pilot, and host admission/index
activation. These are not inferred from passing unit tests.

Build a deterministic manifest with `python aerith/build_release.py`. Only
explicit `PUBLIC_FILES.json` entries can be packaged. An optional `--export`
directory receives exactly those files plus the manifest; it must not exist.
This does not admit, install, push, or run the package. The owner reviews the
result, pins a local commit and manifest hash, and supplies separate admission.

Run behavioral checks: `python -m unittest discover -s aerith/tests -v`.
