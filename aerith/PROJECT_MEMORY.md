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
- A precondition that is not a field of the record is invisible to anything that
  reads only the record. Pass the key a decision is filed under into the decision,
  and let a caller with no key to give be refused rather than excused.
- A claim about how completely something is covered is generated from the code,
  the tests and the mutation results, or it is not made. Three rounds of review
  failed on a hand-written coverage sentence and none failed on the behaviour.
- An extractor that reads a gate must refuse the shapes it was not taught. One
  that skips them reports a subset as the whole, with a machine's authority.
- A mutant killed by a crash in a row proves nothing about the condition it is
  named for. Compare what the row PRODUCED, not which row failed.
- A verdict namespace is not a place to put caller-named keys: a provider filed
  under `verification` overwrote the check of that name and passed the gate.
- A generated number is not a checked number. Make the generator assert its own
  partition before it prints: the categories must sum to the population, and the
  verdicts must sum to their denominator, or it refuses.
- Filter a walk by the LINE it must stop at, not by the top-level statements that
  end before it. A block that encloses the stopping point is dropped whole, and
  everything inside it goes with it.
- A floor that today's count already satisfies cannot fail. Assert an equality
  against a declared number, so adding one changes the declaration too.
- A campaign that edits the live tree owes a clean-tree pre-flight and a sentinel
  that survives a kill: a `finally` runs after an exception, not after a kill.
- Bind evidence to what the run EXECUTED. Binding a file the run never reads buys
  no integrity and costs a re-run for every edit to it.
- Separate what a gate DOES from the account of how completely it is covered.
  Five review rounds found nothing wrong with the first and something wrong with
  the second every time. Report them as two verdicts, never as one.
- A false sentence in an artifact is corrected even when the round that wrote it
  is being abandoned. Abandoning the work is a decision; leaving the record wrong
  is a defect.
- When a correction cannot be made without invalidating retained evidence, leave
  the artifact alone, write the correction where the reader will meet it, and name
  the fix as the first thing the next run must land. Never edit evidence quietly.
- A coverage instrument must not run inside the mutation campaign it reports
  on. A check that the inventory matches the registry fails whenever a condition
  is deleted, so every such mutant dies of bookkeeping and the campaign measures
  the instrument instead of the tests.

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
- 2026-09-15: Review of that change found the stronger mode left unbound: the recorded mode's
  record was checked against its evidence, but a `provider-response-header` record built on
  recorded evidence (zero header calls) passed `validate_capability`. The check now requires an
  admitted mode for every provider record and, under the header mode, every evidenced positive
  call on header evidence with none on the recorded tier and no withdrawal. The package's own
  suite tests both modes against a real evidence file. Forward rule: a check added for one
  mode is added for every mode the same function admits, strongest first.
- 2026-09-15: Adding the header mode to the existing rewrapped-evidence test made it pass for the
  wrong reason: its model attestation stub was now refused by the new binding with the same message,
  so deleting the evidence time and executable checks survived every suite. The binding test now
  changes one evidence-to-proof field at a time against evidence the attestation check admits, each
  with a mutant. Forward rule: when a new guard sits in front of an existing test's target, give the
  test input the new guard admits, or the test stops testing its target.

- 2026-09-16: Three review rounds in a row were spent certifying an attestation label that no
  observation on this backend can substantiate. D10 removed the certification and left the label
  as the operator's runtime evidence floor, moving the model choice to an explicit declaration at
  start. Two mutants then survived because their rows drove the failure through the
  `model_attestation` case, where the one surviving invariant answers first, so removing the
  per-case `passed` and the non-empty measurement checks changed nothing observable; both rows now
  drive it through a case that invariant does not touch. A third was equivalent by construction.
  Forward rules: never ask a gate to certify a property the evidence cannot establish, because the
  reviews will be spent on the label rather than the fact; and when a row for condition B must pass
  through condition A, route it through an input A does not judge, or the row proves nothing about B.

- 2026-09-16 (round 23, answering the round 22 reviews): the display built to replace a removed
  certification repeated the defect it replaced. `attestation_facts` was written to report what the
  evidence measured, and then took the proof's AGE from the record rather than from the evidence, so
  a proof rewrapped around old evidence printed as fresh and warning-free beside the very refusal it
  caused, and `doctor` printed that panel next to the refusal with nothing saying the gate had
  refused. Two more of the same shape: the removal of the attestation certification also removed the
  requirement that a record NAME a floor, which certifies nothing and only says which tier the call
  path will enforce, so the readiness surfaces reported a record ready when every launch of it was
  certain to be refused; and a warning that fired on every admissible record made the two warnings
  that carry information unreadable. Forward rules: when a check is replaced by a DISPLAY, every
  field on that display must name where it came from, and the gate's verdict must travel with it,
  because a panel beside a refusal is read as a verdict whether or not it claims to be one. When a
  check is removed for being unfounded, split it first: the half that asserts something about the
  world goes, the half that only requires the record to be well formed stays. And a warning that
  cannot be absent is not a warning, it is a sentence, so put it in the statement.

- 2026-09-16 (round 23): a review's own input gap is a defect of the round, not of the reviewer. Both
  round 22 reviewers reported that `review_r21_sec.txt` did not exist and worked from second-hand
  summaries; the same gap had been reported in round 21 and was recorded as fixed when only three of
  four files had been saved. Forward rule: before dispatching a review, assert that every path the
  brief cites exists, and treat a reviewer's reported input gap as a finding against the round that
  dispatched it.

- 2026-09-16 (round 24): the fix for a display that reported an unbound measurement was two more
  checks on the display; the reviews then found the layer under each one. What ended it was deleting
  the second reader: the gate already reads and binds the evidence, so it now hands the measurement
  back and the summary reports only that. Six findings went with the design rather than with patches,
  because a summary that opens no file needs no path, size or reparse bound and cannot be an oracle,
  and a summary that reports nothing on a refusal cannot report it reassuringly. Forward rule: when a
  display and a gate read the same artifact, the display is not a reader; make the gate hand back what
  it validated, or the display will be the weaker of two readers and will eventually disagree with the
  stronger one.

- 2026-09-16 (round 24): three mutants that survived were the code telling the truth, not gaps.
  `len(template) == 3` was subsumed by the template equality beside it, the `at` spelling of the
  evidence check time was dead, and the script-in-argv check became unreachable once the launchable
  shape pinned argv[1:]. Forward rules: a mutant that cannot die is a claim about the code, so read it
  before writing a test for it - delete a subsumed condition, delete a dead one, and record an
  unreachable one as owed to the purpose that can still reach it. And two mutants that reported
  HARNESS_FAILURE were deleting a block header and leaving an empty body: a mutant must change
  BEHAVIOUR, never syntax, or it measures the module loader instead of the tests.


- 2026-09-16 (round 25): the round 24 fix closed four of the five preconditions a launch has, and
  both reviewers found the fifth independently. The missing one was the vendor KEY the adapter is
  filed under, which is not a field of the record, so a function that took only the record could not
  see it; the host pin is keyed on the record's digest, so one legitimately pinned adapter copied
  under a second key was pinned too, reported `proof-current` by both readiness surfaces, and
  refused at every launch. Forward rule: pass the key a decision is filed under INTO the decision,
  make it required, and refuse a caller that has none rather than excusing it.

- 2026-09-16 (round 25): rounds 22, 23 and 24 all failed review on the ACCOUNT of completeness and
  none on behaviour. The answer was to stop writing the account. The conditions are now extracted
  from the gate's source by `tests/gate_inventory.py`, each one carries a disposition in
  `tests/gate_conditions.json`, and the paragraph in the plan is emitted by a ledger that refuses
  when the code, the registry, the rows and the mutation results disagree. Two properties matter
  more than the automation: the extractor REFUSES any code shape it has not been taught, because one
  that skipped a shape would report a subset as the whole with a machine's authority; and isolation
  is measured from what each row PRODUCED, so a mutant killed by crashing its row is reported as
  killed by a crash rather than counted as proof of the condition it is named for.

- 2026-09-16 (round 25): the `at` spelling deleted in round 24 was recorded here as a dead
  condition beside two that genuinely were subsumed or dead. It was neither. An evidence file whose
  top-level check time is spelled `at`, with `proof.checked_at` equal to it, was ADMITTED before the
  deletion and is REFUSED after it, so the deletion was a deliberate narrowing of admission. The
  falsifier was run against all three retained live-run evidence files and every one spells it
  `checked_at`, so nothing held is refused and no live run is owed. Forward rule: a deletion that
  changes what is admitted is a narrowing, and calling it the removal of an unfalsifiable condition
  hides the decision from the next reader.

- 2026-09-16 (round 25): two defects were found while answering the reviews rather than by them.
  `doctor` kept provider verdicts in the same mapping as its own checks, so a provider filed under
  `verification` had its refusal overwritten by the sandbox check of that name and could not affect
  the verdict. And the two-repo test asserting that a live-probe adapter is admitted by the package
  gate had been RED since D10 in round 22, asserting three refusals D10 deliberately removed, in a
  lane that nothing runs. Forward rule: a test in a manifest nobody runs is not coverage, and a
  shared namespace between verdicts and checks is a place for one to overwrite the other.

- 2026-09-16 (round 25): the coverage instrument's first campaign caught the instrument. Three
  mutants that delete a condition died only in the test that checks the extracted inventory against
  the registry, because deleting a condition is exactly what makes those two disagree; no behaviour
  test noticed them. Reported as written, "89 of 89 killed" would have counted the instrument's own
  paperwork as coverage of the gate. The instrument's tests moved into `tests/test_inventory.py`,
  which the suite runs and the campaign does not, and the two conditions left without a killer were
  given real rows. Forward rule: a control that reports on a suite must not run inside the
  measurement it reports on, and a survivor list predicted before a run is a claim to check against
  the run, not a result to write down afterwards.

- 2026-09-16 (round 26): both round 25 reviews FAILED and all four P2s were in the instrument, none
  in the gate; two were found independently by both reviewers. The extractor had claimed to refuse
  any shape it was not taught and instead dropped one silently: an `if` whose body neither exits nor
  assigns a decision. Three live guards were invisible that way, and one of them decided whether
  argv[0] is hash-locked at the moment of launch. The launch mirror had the same shape of hole one
  level up: it filtered `invoke`'s top-level statements by end line, which discarded the whole `with`
  block holding the launch and the refusal inside it, and its `>= 4` floor could not fail when a
  fifth precondition was added. Forward rules: record every guard, including the ones that decide
  nothing, because "recorded as deciding nothing" is a different statement from "not recorded"; walk
  to the line you must stop at rather than filtering the statements that end before it; and assert an
  equality against a declared count, never a floor the present already satisfies.

- 2026-09-16 (round 26): the generated block reproduced, in a machine, the failure the machine was
  built to end. It printed a three-way split of a five-way population, an isolation sentence that
  summed to one more than its own denominator, and a heading promising that a reader could disagree
  with every judgement while one whole disposition was printed nowhere. Nothing on the path compared
  the sentence with the data. The generator now asserts both sums before it prints and refuses when
  they do not close, and that refusal caught a real registry entry on its first run rather than a
  planted one. Forward rule: a generated number carries a machine's authority and none of its
  arithmetic; make the generator check its own partition, or it is prose with better formatting.

- 2026-09-16 (round 26, and the decision to stop): the reviews failed a fifth time, again on the
  account and not on the gate, and this time inside the section written to hold the account: the new
  OUTSIDE heading stated 27 entries as 2 + 24 where the 2 were a subset of the 24, so three entries
  were printed nowhere, and those three were the vendor-key delegation, the model-attestation
  invariant and the launch-time hash-lock guard the round was built to record. Both reviewers found
  it independently. Master Bank ruled to stop and record the rest as residual risk rather than spend
  a sixth round inside the instrument. Two false statements from the round were corrected before
  stopping: the arithmetic assert had never refused anything (a different control caught the entry
  the round credited to it), and four guards had been invisible rather than three, which the round's
  own diff showed and the round took from the reviewer instead of counting. Forward rules: every new
  mechanism creates new surface to be wrong on, so a round that adds one should expect to be graded
  on it; and when the measurement disagrees with the story, the story is what changes.
