# P2P UX — Work in Progress

> *"Every laptop is a microphone. Whoever hears it best, wins."*

This document captures the full context of the P2P UX design process — the decisions, the dead ends, the taste conversations, and where we landed. It's written so anyone (or future-us) can pick up exactly where we left off.

---

## What we built

VoxTerm P2P lets multiple people in the same room share their transcripts over the local network. Each laptop captures its closest speaker best. The combined result is better than any single mic. The user confirmed this in testing:

> *"the transcript coming from the peer closest to them is goooooooood"*

The protocol layer is solid: mDNS discovery, TCP transcript exchange, clock sync, heartbeats, 28 bugs fixed across 7 audit rounds, full E2E test suite. The hard problem was never the networking — it was the UX.

---

## The 6 iterations (and why each failed)

### 1. Session codes (VOXJ-7K3M)
One person creates a session, gets a code, reads it aloud. Others type it in. The code was both the join credential and the encryption key seed.

**Why it failed:** Too much friction. You're using a voice transcription tool and asking people to verbally relay connection codes. The irony was a design smell.

### 2. Three-word codes (bacon-horse-galaxy)
Same model, friendlier codes. Easier to say aloud.

**Why it failed:** Still codes. Still friction. Still a modal to type them in.

### 3. Toast + auto-join
When a peer appears on the network, a non-blocking toast slides up: "halcyon is nearby — ENTER to share." One keystroke to join.

**Why it failed:** On shared WiFi (campus, coffee shop), pressing N sends your transcript to a stranger. Auto-join is a privacy violation. Also, only one toast at a time — multiple peers were silently dropped.

### 4. "Go live" toggle
Press N to go live. Your mDNS flips to `in_session=True`. All live peers auto-mesh.

**Why it failed:** No conversation boundary. Two meetings on the same WiFi become one giant confused transcript.

### 5. Context-sensitive N key
N does different things depending on state: creates if alone, joins if one group, shows picker if multiple, leaves if in a group.

**Why it failed:** Two independent UX reviewers killed it. Context-sensitive keys break mental models. The user can't predict what N will do before pressing it. "N does four things" is a state machine disguised as a key.

### 6. Network bar (current, shipped)
N always opens a one-line network bar. Always the same action. From the bar: C to create, ENTER to join, L to leave, ESC to dismiss. Two keystrokes for everything.

**Why it works:** Predictable, intentional, safe. No auto-join. No context-sensitivity. The user always knows what N will do.

---

## Product direction and taste

### The bar is extremely high

The user explicitly said:
- *"i want it to kind of feel like a fun party that youre joining"*
- *"it should be delightful and just so easy"*
- *"we need to try harder"*
- *"this needs to have guts and a spine and be real and be usable"*
- *"form a perspective about the product"*

This is not a feature that should feel like plumbing. It should feel like magic. The connection flow should be invisible so the transcript — the actual product — can shine.

### The persona reviews

We ran the design through 5 personas. The key takeaways:

**Bret Victor (interaction design):**
- Kill the "room" abstraction. The transcript IS the shared space.
- "The room should be as invisible as the air in the room."
- Don't ask for a name before the user has context. Use OS username silently.
- The 15-second auto-dismiss toast is arbitrary. Either show a countdown or don't auto-dismiss.
- "halcyon left" is noise in the transcript. Presence changes belong in the peer bar, not the transcript. The transcript is sacred — only spoken words.

**Thomas Ptacek (security):**
- "Anyone on the same WiFi can silently capture a verbatim transcript of your conversation with zero interaction required." This is the real threat.
- Don't ship without encryption. "Disabled for debugging" becomes "shipped to users."
- Minimum viable security: 4-digit PIN or QR code for physical-proximity verification. Less friction than 3-word codes.
- mDNS broadcasts should be scoped. Don't proactively announce — respond to queries.
- Add sender authentication (Ed25519 keypair per node) for tamper-evident transcripts.
- Session pinning: once all expected peers join, lock the session.

**Stewart Butterfield (product):**
- The magic moment is when the first peer transcript arrives and it's better than your own mic. This moment needs to be VISIBLE, not silent.
- "callsign" works for cyberpunk but not for meeting rooms. The frame is the personality — the copy should be plain.
- Handle the empty room: "GROUP ● alice (waiting...)" is the app telling you nobody came. Reframe as "visible to 2 nearby."
- Two people pressing N at the same time → two groups. Handle the collision.
- Privacy: add a "quiet" option for shared offices.
- The story: "Everyone's laptop is a microphone, and whoever hears it best, wins."

**Steve Jobs (coherence):**
- The PIN has to go. The room is the trust boundary.
- The toast persistence is wrong. It's nagging.
- [via bob] annotation is metadata clutter. Use color only. One subtle annotation on the very first peer line, then just color.
- "The merge is the product." Get two laptops in a room. Talk. Look at the merged transcript. Does it read like one clean document? If not, nothing else matters.
- "Make the simple thing automatic. Make the rare thing accessible. Make the invisible visible. Hide everything else."

**Interaction designer (UX review):**
- Kill auto-join for single group. Always show the picker. Privacy > convenience.
- Add group_id to mDNS TXT record for multi-group partition.
- N-to-leave needs a confirmation beat. Use N+L (separate from join).
- Footer label should change: `[N] Network` when solo, show group info when connected.
- "scanning..." not "waiting..." — implies active seeking, not passive loneliness.
- Handle name collisions in the picker (two "alice"s).
- Add TTL to discovery entries (stale peers linger).

**Product psychologist (mental model):**
- N should always do the same thing. Context-sensitive keys break trust.
- Auto-join is a showstopper on shared networks. "The default behavior on any shared network is accidental sharing."
- The party is in the transcript, not the UI chrome.
- N-to-leave is the classic toggle-key failure. User presses N to check status and accidentally disconnects.
- First use must be safe. Pressing N out of curiosity should have zero side effects.
- Treat already-shared data honestly: "your transcript was shared for 4m 12s" on leave.

### The principles that emerged

1. **The transcript is sacred.** Only spoken words belong in it. No join/leave notifications. No metadata.
2. **One key, one action.** N always opens the network bar. Predictable.
3. **Two keystrokes for everything.** Intentional but not burdensome.
4. **Safe exploration.** N → read → ESC = zero side effects. No accidental sharing.
5. **The magic is in the content.** The first time a peer transcript arrives and it's clearer than your own mic, that's the product. Surface it with a one-time annotation, then get out of the way.
6. **Groups, not rooms.** Named after people, not codes. The group is the multi-conversation boundary.
7. **Privacy by default.** Nothing shared until you explicitly join. No auto-connect.

---

## What's shipped (current state)

### Network bar (N key)

Not in a group:
```
[C]reate  ·  ▸ alice's group (2)  ·  dave's group (1)  ·  ESC
```

In a group:
```
GROUP ● alice ● bob 2ms ● carol 3ms  ·  [L]eave  ·  ESC
```

### Telemetry bar

```
● REC    qwen3-1.7b    English    GROUP ● alice ● bob 2ms ● carol 3ms
```

or when not in a group:

```
● REC    qwen3-1.7b    English    2 peers nearby
```

### First peer transcript annotation

```
— shared via alice's mic —
[14:03:25]  alice:Speaker 1   I think we should go with option B
```

### mDNS TXT record

```
node_id, display_name, group_name, in_session, tcp_port, udp_port, proto_v
```

---

## What's NOT shipped (known gaps)

### Encryption
Currently plaintext. Ptacek says don't ship without it. The crypto module (AES-256-GCM) exists and was tested. The key exchange UX is the unsolved part. Best candidate: derive key from sorted(both_node_ids) exchanged in HELLO — zero friction, prevents passive sniffing, doesn't stop a determined LAN attacker.

### Transcript deduplication / "best mic wins"
Currently both sides display all transcripts interleaved. No dedup. When Alice and Bob both capture the same utterance, it shows up twice. The "best mic wins" merge algorithm is the next major feature. Candidates: compare audio energy, transcription confidence, speaker proximity estimation.

### Audio streaming (UDP)
The `AudioStreamer` class and `PeerAudioBuffer` exist and are tested. Not wired into the app yet. This is the path to multi-channel processing — using N mics to produce a better-than-any-single-mic signal.

### Progressive trust / remembered peers
Currently every session is fresh. No "trusted peers" list. The AirPods model (first time consent, then auto-reconnect) was discussed but deferred to keep V1 simple. Could be added later: config stores trusted node_ids, auto-connect when both in same group_name.

### Session pinning / locking
Once all expected peers join, lock the session to prevent new connections. One keystroke. Discussed by Ptacek, not implemented.

### Quiet / non-broadcast mode
For shared offices where you don't want your group visible to the whole network. Your group exists but only people who know your display name can find it. Discussed by Butterfield, not implemented.

---

## Technical state

- **28 bugs fixed** across 7 audit rounds
- **235 unit tests** + **12 E2E tests** passing
- Protocol: mDNS discovery, plaintext TCP, heartbeat clock sync
- Files: `network/` (8 modules), `widgets/network_bar.py`, `app.py` integration
- Branch: `experimental`
- Spec: `docs/p2p-protocol-spec.md`

---

## Where to pick up

1. **Test the network bar on two real machines.** The E2E passes on loopback but the real test is two laptops in a room.
2. **Re-enable encryption** with zero-friction key derivation.
3. **Build the merge algorithm** — this is the product. "Whoever hears it best, wins" needs to be real, not just interleaved.
4. **Consider progressive trust** for repeat collaborators (auto-reconnect).
5. **Polish the annotation** — the `— shared via alice's mic —` line. Make it feel alive, not clinical.
