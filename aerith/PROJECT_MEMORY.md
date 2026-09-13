# Project Memory

## Forward Rules

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
