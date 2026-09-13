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
- A failed ticket needs a persistent attempt cap independent of changing code.
- Read-only review must not change ticket completion or create delivery commits.
- A Windows pathname is not a Linux container host-path probe. Establish host
  exclusion from the daemon's exact inspected mount set before starting code.
- Pin the local Docker endpoint and identity, clear client context/proxy config,
  prohibit implicit image pulls and override image ENTRYPOINT with approved argv.
- Requested-model metadata is not automatically observed-response metadata.
  Validate each provider's envelope provenance instead of inventing attestation.
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
