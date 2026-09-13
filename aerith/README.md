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
  gates, P3 dispositions, and separate ticket/final review packets. Every ticket
  has a canonical hash-bound Markdown file. Each review attempt has a durable
  controller nonce bound to the exact source, spec, tickets, tests and scope.
  A failed review cannot be reused as its own re-review, and the integrated
  review after all tickets is a distinct fresh attempt.
- A failed ticket has a persistent three-attempt ceiling even if code changes.
  Repeated failures pause. A new budget requires an explicit operator decision;
  this candidate does not yet offer a budget-reset command.
- Process cancellation before launch, Windows Job ownership before resume,
  POSIX process-group cleanup, bounded output and deadlines.
- Atomic reviewed executable/source hash-to-use binding is enabled on Windows;
  admitted provider and verifier adapters fail closed on other hosts until an
  equivalent native namespace-binding backend is implemented and proved.
- An optional digest-pinned Docker verifier mounts each exact reviewed source
  file separately and read-only, without its containing worktree, network,
  host home, Git metadata or Docker socket. Source files and their Windows path
  namespaces remain locked through container cleanup. The sandbox class runs
  from the exact source bytes returned by capability validation, rather than a
  later import from a replaceable disk path.
  It runs non-root with capability, memory, CPU and process limits; normal
  cancellation removes its specifically owned container. The explicit
  `probe_docker.py` tests real containment, the exact visible source set, and
  descendant cleanup using canaries. A read-only empty tmpfs shadows any image
  content at `/workspace`; a pre-exec guard accepts only the individually locked
  files and their required parent directories. The adapter, process transport,
  and contract helper all execute from the exact runtime bytes admitted by the
  retained proof.
  Image health checks and daemon log forwarding are disabled and inspected;
  start and cleanup use the immutable created container ID.
- Claude/Codex/Gemini provenance parsers remain available for offline protocol
  conformance, but this candidate's production launcher rejects ordinary vendor
  CLIs. Only the reviewed Codex data-only native worker is currently admitted;
  other vendors require equivalent workers before activation. Native/non-Docker
  verification is likewise rejected because it cannot enforce the frozen source
  packet.
- The Codex data-only edge sends a bounded controller packet plus a strict
  stage-specific JSON schema. It accepts exactly one metadata record and one
  result record whose request, response, requested-model and provider-model
  identities agree, then validates the returned object again against the same
  closed stage schema. Retained capability evidence binds its measurement time,
  executable bytes and runtime closure. On Windows, every mutable path ancestor,
  the executable and each reviewed runtime dependency are held read-only through
  child exit, preventing path retargeting or replacement between validation and
  use. Its reviewed code disables project/ancestor configuration discovery and
  reads only the Codex auth home plus bounded stdin. The Windows transport uses
  a locked, non-writable system CWD for that worker and the fixed Docker client
  to keep DLL lookup away from the project; it does not claim that CWD would
  isolate a general vendor CLI. The source-built
  worker remains a separately reviewed, host-pinned standalone executable with
  no runtime-file closure; no ordinary Codex thread lifecycle is accepted.
- A private GitHub outbox with idempotent markers and read-back. Unknown creates
  are reconciled, never blindly repeated. The initial Issue body is immutable;
  subsequent revisions are idempotent comments to avoid overwriting user edits.
  Local completion and mirror completion are separate statuses.
- Hash-bound package admission with expiry, per-skill revocation, and invocation
  audit. Model and sandbox adapters require retained negative-test evidence.
  Requested models must be independently attested in provider output.
- Capability checks require a separate host-bootstrap allowlist of complete
  reviewed adapter/proof hashes. Caller-created checksums and booleans cannot
  grant authority. Versioned evidence must substantiate the claimed cases and
  repeat the exact proof timestamp, executable hash and runtime-file hashes.
  The trusted operator-owned host policy is outside every worker's write scope;
  this is not a defense against a malicious owner replacing their own policy.

## Host-owned inputs

A run configuration supplies exact `read_set`, `write_set`, approved `tests`
(fixed argv per ID), a private `admission_file`, verified CLI `providers`, and a
verified `verification_sandbox`. Optional `input_artifacts` support standalone
later stages; optional `github` contains a private repository and native CLI
path, never a token. Do not place these host configuration files in this fork.
An explicit containment probe produces evidence only. It never registers a
capability in the trusted host policy or admits a skill automatically.

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

The multi-ticket E2E fixture proves sequential tickets, P2 blocking and fresh
re-review, P3 disposition, final integrated review and a local branch commit.
It remains synthetic and does not prove a live Sol or other provider worker.

Still required before release: live Claude/Codex/Gemini conformance, actual
account model availability/attestation, automatic new-run discovery, sandbox
proof admission, native background scheduler registration and login recovery,
GitHub parent/subissue relationship integration and a live mirror test,
real single-task pilot, and host admission/index
activation. These are not inferred from passing unit tests.

Observed host tests include real Docker denial/descendant-cleanup probes,
the actual verification route catching a seeded wrong-record edit, and short
Claude calls with two exact models in separate tool-less sessions. Those scoped
observations do not establish a full live pipeline or cross-vendor readiness.
Provider acceptance remains host-scoped: these strict Codex/Gemini parsers do
not admit a runtime by themselves, and a requested model selector must not be
relabeled as observed response metadata to obtain a passing gate.
Dead-worker Docker-container sweeping at login remains an integration gate;
the deterministic identity currently supports reconciliation before that test
is run again, not an installed startup cleanup service.

Build a deterministic manifest with `python aerith/build_release.py`. Only
explicit `PUBLIC_FILES.json` entries can be packaged. An optional `--export`
directory receives exactly those files plus the manifest; it must not exist.
This does not admit, install, push, or run the package. The owner reviews the
result, pins a local commit and manifest hash, and supplies separate admission.

Run behavioral checks: `python -m unittest discover -s aerith/tests -v`.
