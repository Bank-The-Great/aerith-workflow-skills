# Project Memory

## Forward Rules

- A review cache key is not a review lifecycle. Persist a controller-owned
  attempt identity bound to the exact source, spec, ticket artifacts, test
  evidence and scope. Clear it after any blocking result so a fix receives a
  genuinely fresh re-review, and give the post-ticket integrated review its own
  attempt. Otherwise an unchanged or reverted snapshot can replay an old failed
  report forever while appearing to have asked an independent reviewer again.
- A ticket aggregate is not a canonical per-ticket record. Persist one bounded
  Markdown artifact per stable ticket ID, bind every file hash into run state and
  receipts, and refuse aggregate/file divergence before implementation or review.

- Preserve exact newline bytes when hashing source packets. Windows text-mode
  reads normalize CRLF and can invalidate an unchanged file's write precondition.
- Reconcile uncertain remote mutations before retrying them.
- Tool-less model proposals do not sandbox generated tests. Verify test-process
  containment separately before enabling autonomous delivery.
- Publish only the explicit file manifest. Never include private runtime inputs,
  host settings or proprietary vendor-system prompts.
- Bind current run data, not just event summaries, into the integrity ledger.
- Runtime admission must supply the exact already-verified skill/reference bytes
  to the controller. Reopening a package path while building a provider packet
  reintroduces a verify-to-use race even when invocation rechecks the manifest.
- Git is executable code, not a benign file reader. Admit one absolute hashed
  executable from a directory the active token cannot augment; disable ambient
  system/global config and hooks; compare raw blobs, create objects without
  path-aware filters, and prove each committed blob equals the reviewed receipt.
- A path allowlist does not bind a Windows file identity. Hold non-reparse root
  ancestry and the opened target through mutation, write through that same
  handle, and verify the bytes before releasing it. Until safe create/delete
  primitives are separately reviewed, scope the first release to existing
  regular tracked UTF-8 files with unchanged modes.
- A failed ticket needs a persistent attempt cap independent of changing code.
- Read-only review must not change ticket completion or create delivery commits.
- A Windows pathname is not a Linux container host-path probe. Establish host
  exclusion from the daemon's exact inspected mount set before starting code.
- Pin the local Docker endpoint and identity, clear client context/proxy config,
  prohibit implicit image pulls and override image ENTRYPOINT with approved argv.
- Requested-model metadata is not automatically observed-response metadata.
  Validate each provider's envelope provenance instead of inventing attestation.
- A provenance field names the tier of evidence it rests on. Never put recorded
  or echoed evidence in a field whose name claims the answering model.
- Pin the runtime's entire executable closure, not just a launcher or bundle.
  Source-built CLI provenance does not by itself prove tool-less containment.

## Lesson Log

- 2026-09-13: The first full-pipeline fixture failed because Git checked out CRLF
  while snapshot read_text normalized LF. Decode read_bytes for exact identity.
- 2026-09-13: The fork API initially returned HTTP 500 with an empty body.
  Read-back prevented an unsafe duplicate workaround. A later authorized retry
  succeeded as a true public fork; adaptation commits remain local.
- 2026-09-13: Independent audit found retry-counter resets, post-launch-only
  cancellation, incomplete runtime proof hashes, and state rows outside the
  event hash chain. Behavioral tests now cover these failure classes.
- 2026-09-13: A Windows sandbox's external-write denial did not establish
  external-read or network denial. Synthetic probes demonstrated the gap;
  production activation remains blocked pending a proved isolation backend.
- 2026-09-13: Docker Desktop was explicitly authorized. Real source-mount,
  network, root-read-only and descendant-cleanup probes passed after independent
  review corrected a cross-OS pathname false positive, ambient daemon routing,
  implicit pulls and image-entrypoint ambiguity. The actual verification route
  rejected a seeded wrong-record change. Startup crash sweeping is still pending.
- 2026-09-13: Claude's usage envelope initially included a utility-model call.
  Disabling nonessential traffic removed that auxiliary call. A live probe then
  verified two exact task models through assistant-message and usage metadata,
  in distinct sessions with tools/MCP disabled. This is not full-pipeline proof.
- 2026-09-13: Added strict parsers for separate source-built response-provenance
  runtimes. Reject missing/mixed identities, stale attempts, effect-capable
  events and incomplete streams. Sixty behavioral tests pass. Neither parser
  grants runtime admission; both require host-pinned conformance evidence.
- 2026-09-13: A lifecycle-free Codex worker needs a distinct wire contract,
  not a filtered ordinary thread stream. The controller now sends strict
  per-stage schemas and accepts exactly two identity-bound JSONL records;
  executable containment and live subscription proof remain host-owned gates.
- 2026-09-13: Provider-side structured-output enforcement is not an integrity
  boundary. Validate the object locally against the same closed stage schema,
  and bind retained evidence to its own timestamp, executable bytes and runtime
  closure. On Windows reject reparse components and hold the reviewed executable
  deny-write/delete until the suspended owned child is resumed.
- 2026-09-14: A final-file lock does not bind a Windows pathname when a writable
  ancestor can be renamed or a junction retargeted. Hold every mutable ancestor,
  the executable and each reviewed runtime dependency through child exit; apply
  the same proof to provider, native-verifier and Docker-client launches. Also
  reject every unrecognized provider-stream record instead of filtering for the
  records the controller hoped to receive.
- 2026-09-14: Capability evidence collected in one project directory does not
  authorize a provider in another directory with different local hooks or
  instructions. A controller-created empty temporary directory was the first
  mitigation, but this was superseded by the neutral-CWD rule below because its
  child namespace remains writable. Pass project material only in the bounded
  packet.
  Process ownership cleanup must begin immediately after Popen because thread
  allocation/start can fail before the normal polling loop begins.
- 2026-09-14: An empty provider directory can become ambient input if a preceding
  auth-status process shares it and leaves a local instruction/config file.
  Giving auth and inference different empty directories prevented cross-call
  residue but did not prevent concurrent same-token injection; this mitigation
  is superseded by the neutral-CWD rule below.
- 2026-09-14: A hash followed by a later import or directory mount is still a
  hash-to-use race. Execute the Docker sandbox class from the already validated
  source bytes. Bind source as individually mounted, read-only files and hold
  every source file plus its namespace until container cleanup; never mount a
  same-user-writable packet directory whose tests can be replaced or augmented.
- 2026-09-14: A Windows directory handle does not prevent new child entries.
  Never claim an empty user-owned CWD is immutable. A first revision moved the
  child to a non-writable system directory, but writable drive-root ancestors
  made that insufficient for a general CLI that performs ancestor discovery.
  That general-CLI mitigation is superseded by the data-only-worker rule below.
- 2026-09-14: Individually mounted files do not hide pre-existing image content
  at their parent directory. Shadow `/workspace` with an inspected read-only
  empty tmpfs, then verify the exact visible entry set before exec. Runtime proof
  must bind and execute the Docker adapter's full local import closure, not only
  its top-level source file.
- 2026-09-14: Admit provider execution only when an exact reviewed standalone
  data-only worker disables project/ancestor discovery and accepts project data
  solely through bounded stdin; use the system CWD only to remove current-dir DLL
  injection, never as proof about generic CLI instruction discovery. Reject
  ordinary vendor CLIs until they have equivalent workers. Likewise reject the
  native verifier path: only the exact-packet Docker route binds tests to the
  controller's frozen source snapshot.
- 2026-09-14: A reviewed executable's containing directory can still be extended
  with a new dynamic dependency. Provider admission therefore requires one
  standalone native executable with an empty runtime-file closure, and the fixed
  Docker client runs away from the project under an explicit empty client config.
  The only Python verification closure is compiled from its already validated
  adapter, process and contract bytes with local imports injected explicitly.
- 2026-09-14: Sol review found three remaining verify-to-use seams: package
  resources were reopened for packets, Git was found by name and could invoke
  clean/process helpers, and approved Windows pathnames were not held across
  writes. The controller now consumes frozen host-verified resource bytes,
  admits one hashed non-augmentable Git executable, materializes exact blobs in
  a no-checkout worktree, constructs commits with plumbing and rechecks every
  blob, and performs existing-file updates through a locked verified handle.
- 2026-09-14: Resolving a caller-supplied project path before admission follows
  a replaced junction. Preserve the lexical absolute path, hold its non-reparse
  ancestry during use, and persist the opened Git metadata directory's object
  identity so replacement is rejected rather than silently rediscovered.
- 2026-09-14: Git discovery is not a continuing authority. Admit the primary
  metadata directory once, use an exact hash-pinned core executable with an
  explicit Git directory, work tree and branch ref, and disable replace refs,
  lazy fetch, hooks, filters and ambient configuration on every invocation. A
  plain isolated source directory should not contain a rediscoverable `.git`.
- 2026-09-14: Rewriting canonical state in place is not crash recovery. Write a
  unique same-directory temporary, flush and read it back, replace atomically,
  and keep locked worktrees outside canonical state directories on Windows so
  path-protection handles do not prevent the atomic rename.
- 2026-09-14: A process-level DLL search call in `main` is too late for imports
  performed by the Windows loader. A standalone worker needs loader-time PE
  `DependentLoadFlags=0x800`, zero delay imports and an exact audited system-DLL
  allowlist in addition to its pre-main runtime guard. Host probes must verify
  and memory-load package bytes before importing any candidate module and must
  use an explicit pinned Git executable rather than ambient PATH.
- 2026-09-14: Worktree creation and ordinary implementation are different
  lifecycle states. Persist a materialization boundary before dispatch, resume
  an interrupted copy only from the exact branch blobs, and never compare later
  implementation edits to the original branch as though construction were
  still incomplete.
- 2026-09-14: A crash-safe atomic replace can still leave its private temporary
  after abrupt process death. Give controller temporaries a closed naming
  format and sweep them on writable-store startup only after an exclusive file
  handle, reparse check and parent-object identity check; an active writer's
  sharing lock makes recovery skip its live temporary.
- 2026-09-15: The first live call of the source-built Codex data-only worker
  failed closed with `model_metadata_missing`: the ChatGPT backend sends no
  `openai-model` header on a normal response, so header-only provenance could
  never pass live. The worker now names two tiers, and `parse_codex_data_only`
  accepts `evidenced_model` plus `model_evidence` in place of `provider_model`.
  Its `recorded_response_model` source says "without reroute signal", because a
  response object that records the requested model does not prove which model
  answered. Host admission pins were not changed.
- 2026-09-15: Review found the narrowed claim growing back one step later: the data-only
  provider runs only under attestation mode `provider-response-header`, yet it accepted
  and audited a `recorded_response_model` result as `model_attested`. It now refuses any
  tier other than `provider_response_header` under that mode, records the refusal as a
  `provider_failure` event naming the tier, and never writes an attested model for it.
  Admitting recorded evidence needs its own named mode and an operator ruling.
- 2026-09-15: The operator admitted recorded evidence under its own mode after live run 2.
  The data-only provider now accepts `recorded-response-model` (header-tier and
  recorded-tier results) and audits `model_recorded` with the mode, never
  `model_attested`; the header mode is unchanged. `validate_capability` refuses a
  recorded-mode record unless `proof.limits` is exactly `answering_model_proven: false`
  and a boolean `echo_tested` equal to the evidence's `unserved_echo_observable`, and
  refuses evidence showing the recorded tier withdrawn. Forward rule: an attestation
  mode lives in the configuration that the evidence hashes, so a weaker claim cannot be
  relabelled onto old evidence; it needs a new run under that mode, and limits that
  depend on the run's outcome belong in the proof, checked against the evidence.
