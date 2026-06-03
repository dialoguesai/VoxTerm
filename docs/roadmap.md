# VoxTerm Roadmap

_Drafted 2026-06-02 from a panel-of-experts review of the codebase. Six domain experts (segmentation, acoustic robustness, diarization, P2P, redaction, transcript push) dissected the tree, an architect synthesized, and three adversarial reviewers re-grounded every claim against the actual branches. File and line references are from `main` at the time of writing and should be re-verified before work starts._

## Thesis

VoxTerm's identity is "the transcript you can trust": local first, offline, accurate, speaker aware, and safe to share. Everything downstream (speaker attribution, sharing, redaction, sync) is only as good as the raw transcript, and nothing should leave the machine that hasn't been redacted.

That forces a real dependency order, and it survived three independent adversarial reviews:

1. Fix the foundation (segmentation plus acoustic robustness), because clean segments are the substrate for every other capability.
2. Raise speaker attribution to conversation aware, which requires a `Segment` join-key the codebase currently lacks.
3. Build redaction as a fail-closed gate before consolidating push.
4. Only then unify push and harden P2P.

## Stop the bleeding first (v0.1.x hotfix, before the roadmap proper)

Three live privacy holes exist on `main` today. These are shipped bugs, not roadmap features, and they contradict the README's "no cloud APIs / nothing leaves your machine" promise.

1. **`hivemind_mode` defaults to `auto`** (`config.py:255`). On that default VoxTerm auto-discovers any LAN `_sr-hivemind._tcp` sink and POSTs raw transcript text plus speaker names off-device the instant one appears, over plaintext HTTP, with no opt-in and no sink-pubkey verification. Off-device egress must be opt-in. Flip the default to `off`.
2. **`session_code` is broadcast in the mDNS TXT record** (`discovery.py:177`, written at `party.py` group update, read back at `discovery.py:258`). The AES-256-GCM party-mode session key is HKDF-derived purely from that code, so any passive LAN sniffer can recover the key and decrypt party mode. The `discovery.py` docstring falsely claims the code is never broadcast. Remove it from the TXT record.
3. **The hivemind push has zero redaction.** Add a regex-only, fail-closed `RedactionFilter` inside the hivemind client before any batch serializes (`hivemind.py` post path), and switch the POST to https-or-warn. This is superseded later by the full tiered detector, but it ships now so re-enabling egress can never leak raw structured PII.

## Phase 0: branch reconciliation, hotfixes, de-risking spikes

Nothing in Phases 1 to 4 proceeds until the base branch is chosen and "already-shipped" claims are re-grounded.

The blocker that almost derailed planning: branch geography is contradictory. `hivemind.py` (742 lines) and the auto-on egress live only on `main`; the `derp` branch holds the AES-GCM and merger work but deleted `hivemind.py`; `session_recorder.py` exists on no branch. You cannot gate redaction on a file that is not on your base.

- **[S] Choose canonical base branch and write a verified file/feature inventory.** Recommend `main` (where the real holes and the canonical push path live), then port `derp`'s crypto/merger forward as an explicit task. No item may claim "already-shipped" until this resolves.
- **[M] Hotfix bundle.** The three holes above, shipped as v0.1.x.
- **[M] Port `derp`'s AES-GCM / merger network stack onto the canonical base.** Hidden prerequisite for Phase 4 P2P hardening and for verifying encryption status. Note: encryption is already on in `session.py` (`send_encrypted_msg` is used everywhere, `recv_plaintext_msg` is dead). The work is the consolidation, not "turn encryption on."
- **[S] Spike (go/no-go): does the default Qwen3-ASR backend expose per-token logprobs and segment timestamps?** `no_speech_threshold=0.5` exists only in the whisper fallback (`transcriber.py:194`); the default Qwen3 path returns bare text. Two downstream items silently assume otherwise: the Phase 1 hallucination veto (needs logprobs) and the Phase 2 `Segment(t0,t1)` model (needs timestamps).
- **[S] Spike (go/no-go): grove-redaction-eval adapter.** Confirm `voxterm redact --stdin` matches the eval's I/O contract. grove-redaction-eval is a separate private repo, not a checked-out sibling, so do not couple a privacy gate to it; the detector must be unit-testable on in-repo synthetic fixtures.

**Exit:** canonical base chosen and inventory written; the three holes fixed and released; the Qwen3 spike has a definitive yes/no on logprobs and timestamps; the grove-eval adapter either works or its leakage number is reclassified as a follow-up bar, not an exit gate.

## Phase 1: transcript quality foundation

Make the raw transcript clean: cut on real boundaries, not clock ticks, and stop hallucinating on steady-state noise. This is the "cutoffs are lackluster" and "AC noise wrecks them" complaint, and it is about conversation flow, not chunk size.

- **[S] Build the offline segmentation and noise eval harness (representative WAVs).** Hard predecessor. None exists today (`tests/test_vad.py` only tests VAD directly). A few WAVs (monologue, two-person turn-taking, AC-only) replayed through the trigger logic with pre/post counts is the regression guard for every tuning item.
- **[S] Tune endpointer thresholds plus asymmetric VAD hysteresis, in one shared endpointer.** `SILENCE_TRIGGER_SECONDS` 0.3 to ~0.8 (0.3s is below the conversational pause floor, so it fires mid-utterance), `MAX_BUFFER_SECONDS` 3 to ~12 (the 3s cap slices mid-word), plus an onset gate and offset hangover. `tui/app.py` and `dictation/loop.py` duplicate this logic (the loop header literally says it mirrors app.py), so extract one shared endpointer so they cannot drift.
- **[M] Adaptive SNR-relative gate, mic-only VAD routing, ~85Hz high-pass.** No acoustic front-end exists; the fixed 0.5 VAD and absolute RMS 0.005 pre-guards (`transcriber.py`, four call sites) stay open on AC/fan noise, flooding the ASR with noise it hallucinates into text. Land as three flag-gated, independently-profiled sub-changes. (Note: PR #141 already added a high-pass for HVAC rumble on `main`; reconcile against it.)
- **[M] Wire `get_speech_segments()` plus audio tail carry-over.** `get_speech_segments` (`audio/vad.py:109`) is the clause-aware segmenter but is dead code, called only from a benchmark. Wiring it plus retaining a ~0.75s audio tail (instead of get-and-clear) gives cuts on real silence gaps with context continuity. Gated on the Qwen3-timestamp spike if sentence-final logic is used.
- **[M, optional] Hallucination veto via Qwen3 logprobs.** Only if the Phase 0 spike confirms Qwen3 emits logprobs; otherwise it degrades to whisper-fallback coverage and is dropped. Not an exit gate, because the SNR gate plus high-pass already kill most noise upstream.

**Exit:** zero transcript lines emitted from AC/fan-only audio on the eval corpus; measurable reduction in mid-clause splits and mid-word cuts vs v0.1.0; hot path stays within the sub-5ms/chunk budget (each SNR sub-change profiled independently); the TUI loop and the headless dictation loop share one endpointer.

## Phase 2: conversation-aware speaker attribution

Lift speaker assignment from acoustic-only cosine to conversation aware, and build the `Segment` join-key the codebase lacks. This is the "intelligent speaker assignment from conversation analysis" idea.

- **[M] Define the `Segment` data model, stable transcript entry IDs, and the SQLite-store migration path.** Today transcript entries are positional tuples with no id (`transcript.py`), and embeddings are keyed only by `speaker_id` in `speakers/store.py` with no back-pointer. A `Segment` (id, t0/t1, text, embedding, sid, confidence) is the join-key that unblocks correction, merge, reassign, and redaction span addressing. Must define the migration between the existing profile DB and the per-segment model, plus a per-segment embedding-retention memory budget (centroid-drift was a real memory bug, issue #83).
- **[M] Spike then build: online LLM tie-breaker on the `is_ambiguous` branch.** The branch is real (`engine.py:347`) but there is zero LLM wiring in `audio/diarization/` today (llama-swap is only an ASR backend, `LLAMA_SERVER_URL` defaults empty). This is net-new cross-subsystem plumbing, flag-gated and default off, with a kill criterion: it must beat cosine on a held-out clip. Only displayed text goes to a 127.0.0.1-bound model, never audio; add a test asserting no non-loopback socket calls.
- **[L] Deferred offline correction pass (segment-graph re-label).** Where the high-value capability lives: retroactive correction of label switches and duration-split mis-attributions the online greedy path commits permanently. Runs batched off the hot path on a silence-gap or session-end trigger. Critically, it must land on cosine plus turn-adjacency alone so it survives if the tie-breaker spike fails; the LLM-fusion variant is gated on that spike.

**Exit:** `Segment` objects flow end-to-end with stable IDs and the profile-DB migration is complete; a line's speaker can be reassigned and re-rendered by id; on a recording with a deliberate label switch plus quick turn-taking, the offline pass corrects attribution; per-segment retention stays within a stated memory budget; any LLM path stays off the hot path, binds loopback-only, and degrades gracefully when llama-swap is absent.

## Phase 3: redaction gate (must precede consolidated push)

Make redaction structurally unskippable and fail-closed at every point where text leaves the machine, anchored on an on-device tiered detector.

- **[M] Tiered detector package with `voxterm redact --stdin`.** The grove eval shows names are 99% of the leak and regex catches ~0.9%. Tier 1 regex nails structured PII; Tier 2 a speaker-name gazetteer from confirmed profiles (`speakers/store.py`) catches in-room people at near-100% precision (VoxTerm uniquely already knows who is in the room, so this signal is free); Tier 3 a stubbed ONNX NER behind a flag, reusing the already-loaded onnxruntime. Unit-test on in-repo synthetic fixtures so shipping does not block on the external eval.
- **[M] Apply-then-send fail-closed gate on all egress paths (hivemind, P2P broadcast).** Egress redaction is one-shot and irreversible-on-send: once a batch POSTs or broadcasts, a local toggle cannot un-send it. These paths get apply-then-send, default off. Supersedes the Phase 0 regex-only hotfix.
- **[L] RedactionLedger, encrypted original-text vault, render-overlay span model (local artifacts only).** Reversible, per-entity, grouped redaction is sound only for local artifacts (export, clipboard, local save), where un-toggling is meaningful. A ledger keyed by normalized entity (one toggle flips every occurrence) plus a vault reusing the `speakers/crypto.py` Keychain key keeps originals encrypted and never in the released artifact. Profile RichLog re-render cost on a large transcript before committing the overlay UI.
- **[M] Gate the export and clipboard boundary with a per-entity review/approve screen.** The safe, latency-free place to ship the full reversible UX first. `get_markdown`/`get_plain_text` (`transcript.py`) take a redaction policy; the modal shows grouped per-entity toggles before writing.

**Exit:** no egress path can emit unredacted text (apply-then-send, fail-closed, default off, unredactable once sent by design); local export/clipboard redaction is reversible per-entity with originals only in the encrypted vault; if the grove-eval adapter exists, `voxterm redact --stdin` produces a measured leakage number, otherwise the detector passes synthetic-fixture tests; RichLog re-render cost is profiled and acceptable.

## Phase 4: unify transcript push and harden P2P

Heal the forking push implementations onto one versioned wire contract behind the redaction gate, and turn Party Mode from a flaky demo into a deterministic, encrypted, single-clean-transcript experience.

- **[L] Single `transcript_push` module on the voxterm-sink-protocol `Transcript` shape.** Three contracts exist: `hivemind.py` on `main` (text batches), the RonTuretzky `upload-*` branches (multipart plus audio), and the frozen TEE spec. Some of that is unmerged experimental work, so consolidation is partly forward-unification, not healing shipped divergence. Adopt the spec's text-segment-shaped `Transcript` (schema_version, content_type, provenance) as the single forward contract over plain HTTPS first; TEE deferred. Salvage the retry queue and secure-write infra from the upload branches.
- **[S] Audio-to-disk: verify-then-act.** `session_recorder.py` exists on no branch and the README "no audio stored" invariant is intact on the current tree. The only WAV write is an in-memory WAVE header in `transcriber.py` for the llama-server upload path. Either cite the actual branch/file where audio hits disk or delete the claim. If audio push is wanted, gate it behind explicit per-session consent plus a README amendment. Do not assert a fabricated regression.
- **[M] Harden P2P discovery/election.** Revert the auto-join behavior in `party.py` to the review-approved explicit picker, collect mDNS results in a fixed ~2.5s window before host-vs-join, converge host collisions by lowest node-id. Encryption is already on and the session_code leak was fixed in Phase 0, so this item is discovery/election only.
- **[L] Audio-merge single-transcription "best mic wins," plus a separate cross-correlation spike.** The mixer feed is already partly wired (`tui/app.py` calls `add_local_chunk`; `party.py` instantiates `PeerAudioMixer`), so feeding live mic into the mixer is the L. The hard open part (clock-aligned cross-correlation across two real mics, single transcription of the mixed signal, within hot-path budget on real WiFi) is a separate de-risking spike gated by the 2-laptop acceptance test. Hard scope cut: no progressive trust, no multi-group, no late-joiner sync.
- **[S] Procure and script the 2-laptop (including AP-isolated) real-WiFi P2P test rig.** Predecessor to all Phase 4 P2P. All current validation is loopback/test_harness, which cannot reproduce mDNS timing, multicast drops, or AP isolation, the exact conditions that break it.

**Exit:** one wire contract emits spec-shaped `Transcript` objects behind the egress redaction gate; the audio-to-disk claim is verified (consent-gated if real, deleted if phantom); two Apple Silicon laptops on real and AP-isolated WiFi reliably converge to one encrypted party with one clean merged transcript (no duplicate lines, correct speaker order); cross-correlation passed its own spike gate before merge.

## Top risks

- **Hot-path budget erosion.** The sub-5ms/chunk VAD/UI discipline is load-bearing; wired re-segmentation, the SNR gate, online LLM tie-breaking, and redact-before-broadcast all threaten it. The eval harness is a hard predecessor, each SNR sub-change is profiled independently, LLM stays batched/off-thread/loopback, and egress redaction latency is measured before enabling.
- **Qwen3-ASR backend uncertainty.** The default may expose neither logprobs (gates the hallucination veto) nor segment timestamps (gates the entire `Segment` t0/t1 join-key). Resolved as a Phase 0 spike.
- **Branch consolidation coordination.** Porting `derp`'s AES-GCM/merger onto `main` (PR-only, auto-merge off) is where shipped-vs-branch behavior has already bitten.
- **Redaction false-negatives on egress are irreversible.** Once a name broadcasts or POSTs it cannot be recalled. Apply-then-send, default off, and the gazetteer's near-100% in-room precision are the safety margin.
- **P2P real-hardware gap.** Loopback cannot reproduce the conditions that break it; the 2-laptop rig is a scheduled predecessor.
- **Offline correction risk.** The largest single item even after severing LLM dependence; if cosine plus turn-adjacency do not beat the online path on a held-out clip, cut it rather than ship on faith.
- **Hivemind sink operator threat.** VoxTerm does not verify the advertised sink pubkey, so any LAN host can spoof `_sr-hivemind._tcp`. The auto-to-off hotfix mitigates the default; sink authentication remains unbuilt and is a known residual.

## Explicitly deferred

- TEE transcript-sink implementation (Dstack/TDX attestation, ed25519 author signing, BLAKE3 content addressing, DCAP quote verification). Adopt the data model now, build the enclave later.
- Sink-operator authentication / advertised-pubkey verification for hivemind.
- LLM-fusion variant of the offline correction pass (only if the Phase 2 tie-breaker spike wins).
- Turn/semantic endpointing via an on-device classifier.
- Neural denoiser (DeepFilterNet3/RNNoise), only if gating plus high-pass leave a real SNR ceiling.
- Full content-aware streaming diarizer rewrite (the tuned 0.52% EER online path is not worth the regression risk).
- Text-dedup "best mic wins" merge (reconciling two disagreeing ASR outputs is tuning-hell).
- Speaker-profile (biometric embedding) export/sync surface (would need the same fail-closed discipline as transcripts).
- Data-retention / right-to-delete for pushed transcripts.
- P2P advanced features: progressive trust, quiet mode, Opus bandwidth adaptation, late-joiner history sync, multi-group, kick/remove.
- Cross-platform beyond landed Phase 1, mobile clients, and the Hivemind MCP server interface.
