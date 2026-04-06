# Party Mode — Design Document

> *"Every laptop is a microphone. Whoever hears it best, wins."*

This document is the definitive design reference for VoxTerm's P2P joining experience. It supersedes `p2p-ux-wip.md` (which remains as history of iterations 1–6). This is iteration 7: **Party Mode**.

---

## The Problem

Six iterations failed because they all treated P2P connection as a **configuration task** — something you set up before the real work begins. Codes to type, modals to fill, bars to browse. Every one of them made the user think about networking before they could think about transcription.

But the human brain doesn't work that way. Walking into a room with friends isn't a configuration task. It's a social event. It should feel like arriving somewhere, not debugging a network.

The core insight: **joining should feel like an achievement, not an errand.**

### What we learned from 6 failures

| Iteration | What it was | Why it died |
|-----------|-------------|-------------|
| 1. Session codes (VOXJ-7K3M) | Type a code to join | Ironic: voice tool asking you to dictate codes |
| 2. Three-word codes | Friendlier codes | Still codes. Still a modal. Still friction. |
| 3. Toast + auto-join | "halcyon is nearby — ENTER" | Privacy violation on shared WiFi. One toast at a time = peers dropped. |
| 4. "Go live" toggle | N flips to live, all live peers mesh | No conversation boundary. Two meetings merge. |
| 5. Context-sensitive N | N does 4 things depending on state | Unpredictable. Broke mental models. Killed by 2 UX reviewers. |
| 6. Network bar | N opens bar, C/ENTER/L from there | Predictable but still 2+ keystrokes. Still browsing. Still feels like configuration. |

The network bar (iteration 6) was the closest — it solved predictability and safety. But it's still *plumbing*. The user opens a bar, reads group names, selects with arrow keys, presses ENTER. That's a file picker for network connections.

### The bar from iteration 6

```
[C]reate  ·  ▸ alice's group (2)  ·  dave's group (1)  ·  ESC
```

This tells the user: "here are some network endpoints, choose one." It's correct. It's not delightful.

---

## Party Mode: The Design

### One sentence

**Press N to go to the party. Press N again to leave.**

### The mental model

N is not "open the network bar." N is not "toggle P2P." N is **"go to the party."**

When you press N:
- If there's a party nearby, you walk in.
- If there's no party, you start one — and you become the host.
- Either way, you're at the party within 2 seconds.

When you press N again:
- You leave the party. Clean break.

That's it. One key, two states: at the party, or not at the party.

### Why this isn't iteration 5 (context-sensitive N)

Iteration 5 failed because N did *four different things* depending on hidden state. The user couldn't predict what N would do.

Party Mode's N does *one thing*: toggle. You're either at the party or you're not. The toggle is always the same direction — if you're out, you go in; if you're in, you go out. No browsing, no creating, no picker. The complexity is hidden behind a single state flip.

The difference from iteration 5: there's no third or fourth state. No "show picker if multiple groups." No "create if alone vs join if one group." Just in/out.

### Why this isn't iteration 4 ("go live")

"Go live" failed because it had no conversation boundary — two meetings on the same WiFi merged into chaos.

Party Mode has boundaries: **groups**. But the user never sees group management UI. The system automatically determines which group to join based on proximity signals (see "Proximity Resolution" below). The boundary exists in the protocol, not in the UI.

---

## The Five Micro-Moments

Every party mode activation has five distinct moments. Each one needs to feel right.

### 1. Intent (pressing N)

The user decides to be social. This is a mode switch — from solo transcription to shared transcription. The UI should acknowledge this shift immediately.

**What happens:**
- The footer changes from `[N] Party` to a scanning state
- The waveform color subtly shifts (the whole app feels different in social mode)
- mDNS discovery activates (or was already running passively — either way, now actively scanning)

**Feel:** Flipping a switch. Committing to being social. Like turning on your camera in a video call — a small act with clear social meaning.

**Time budget:** Instant. The UI change happens on the same frame as the keypress.

### 2. Discovery (scanning for peers)

The app is looking for nearby VoxTerm instances. This usually takes 0.5–3 seconds for mDNS to resolve.

**What happens:**
- Footer shows: `◌ looking for the party...`
- The indicator pulses — not a spinner, a pulse. Alive, not loading.
- mDNS scans, builds peer list, resolves proximity

**Feel:** Walking down a hallway toward a room you can hear music from. Anticipation. Active seeking. Not "waiting" — *looking*.

**Time budget:** 0.5–3 seconds. If no peers found after 3 seconds, transition to hosting.

**Critical detail:** The scanning text is `looking for the party...` not `scanning network...` or `searching for peers...`. The language matters. This is social, not technical.

### 3. Resolution (auto-join or host)

The system has decided what to do: join an existing group, or create a new one.

**Three paths:**

**A. One group found → auto-join (most common)**
```
◌ looking for the party... → joining alice's party... → 
```
No user input required. Zero friction. The system found the party, you're going in.

**B. No groups found → become host**
```
◌ looking for the party... → you're the party now ● waiting for others
```
You start the group. Your display name becomes the group identifier. Others will auto-join you.

**C. Multiple groups found → need disambiguation**
```
◌ looking for the party... →  which party? ▸ alice's (3)  bob's (2) [←→ ENTER]
```
This is the ONLY case where the user makes a choice. And it's a simple one: arrow keys, ENTER. One line, no modal, no configuration. The picker auto-selects the largest group (most likely the one you want).

**On campus WiFi, path C is common.** This is where proximity resolution matters most — see dedicated section below.

### 4. Arrival (connected)

You're in. The handshake is complete, transcripts are flowing.

**What happens:**
- Footer transitions to party state: `● alice  ● bob  ● you`
- Brief moment of visual celebration — a flash, a glow, a pulse. Not confetti. Not a modal. Just... the footer brightens for a moment, acknowledging the connection.
- The waveform may shift hue slightly in party mode (optional, subtle)
- All existing party members see your arrival (anti-spy — see below)

**Feel:** The door opens. You see who's there. They see you. Brief moment of social recognition, then you settle in.

**Time budget:** <500ms for the visual transition after TCP handshake completes.

### 5. First Transcript (the magic moment)

The first peer transcript arrives. It's from alice's mic, and alice is sitting closer to the speaker than you are. Her transcript is *clearer* than what your mic captured.

**What happens:**
- A single, subtle annotation appears above the first peer transcript line:
  ```
  ── via alice ──
  [14:03:25]  Speaker 1   I think we should go with option B
  ```
- That's it. One annotation. Then peer transcripts just blend in, distinguished only by color.
- The quality speaks for itself. The user notices: "wait, that line is better than what my mic caught."

**Feel:** Magic. This is the product moment. Everything before this was plumbing — this is the payoff. The transcript is better because there are more microphones in the room. "Whoever hears it best, wins" becomes visceral.

**Critical:** Do NOT over-annotate. Not `[via alice's mic]` on every line. Not `[remote]` tags. Not metadata. One annotation on the very first peer line, then color only. The transcript is sacred.

---

## State Machine

```
                    press N
    ┌──────────┐ ──────────→ ┌──────────┐
    │          │              │          │
    │   SOLO   │              │ SCANNING │
    │          │              │          │
    └──────────┘ ←────────── └──────────┘
        press N                   │
        (from any                 │  0.5–3s
         party state)             │
                                  ▼
                    ┌───────────────────────────┐
                    │     RESOLUTION             │
                    │                           │
                    │  1 group → auto-join      │
                    │  0 groups → host          │
                    │  N groups → picker        │
                    └───────────────────────────┘
                                  │
                                  ▼
                            ┌──────────┐
                            │          │
                            │ IN PARTY │
                            │          │
                            └──────────┘
```

**N always means the same thing from SOLO:** go to the party.
**N always means the same thing from IN PARTY / SCANNING / PICKER:** leave / cancel.

Two states for the user's mental model. One key.

---

## The Footer: Social Presence Layer

The footer is the always-visible social presence indicator. It's the most important piece of UI in party mode — it tells you your social state at a glance without looking away from the transcript.

### Solo mode
```
● REC    qwen3-1.7b    English                              [N] Party
```

The `[N] Party` label is an invitation. It says: there's a social feature here, press N to explore. It's discoverable without being pushy.

If peers are detected on the network (passive mDNS, always running):
```
● REC    qwen3-1.7b    English                    2 nearby  [N] Party
```

The `2 nearby` is a nudge. People are here. The party exists. You just haven't walked in yet. This is the equivalent of hearing music from down the hall.

### Scanning
```
● REC    qwen3-1.7b    English              ◌ looking for the party...
```

Pulsing `◌` indicator. Active, alive, seeking.

### Picker (multiple groups)
```
● REC    qwen3-1.7b    English    ▸ alice's (3)  bob's (2)  [←→ ENTER]
```

Inline in the footer. No separate bar, no modal. Arrow keys to select, ENTER to join. ESC or N to cancel back to solo.

### In party
```
● REC    qwen3-1.7b    English    ● alice  ● bob  ● you          [N]
```

Each peer gets a colored dot. The dots pulse subtly when that peer's mic detects speech (VAD). You can *see* who's talking before the transcript arrives.

The `[N]` at the end is the exit — press N to leave. No `[L]eave`, no ceremony. N got you in, N gets you out.

### Someone joins your party
```
● REC    qwen3-1.7b    English    ● alice  ● bob  ● carol ✦  ● you  [N]
```

Carol's name appears with a brief `✦` sparkle that fades after 3 seconds. Everyone sees it. The footer is the room — when someone walks in, you see them appear.

No toast. No notification. No sound. Just: a name appears in the footer. If you're looking at the transcript (as you should be), you'll notice it in your peripheral vision. If you glance at the footer, you see the full peer list. This is how physical rooms work — you notice people arrive without being interrupted.

### Someone leaves
Their name fades out of the footer. No announcement. No `carol left` in the transcript. The transcript is sacred.

---

## Anti-Surveillance Design

### The threat

> "Anyone on the same WiFi can silently capture a verbatim transcript of your conversation with zero interaction required."
> — Thomas Ptacek, security review

This is the fundamental tension: auto-join is convenient but enables passive surveillance.

### The solution: mandatory visibility

**You cannot be in a party invisibly.** Period.

1. **The footer is the room.** Every party member appears in every other member's footer, always. You can't join without your name appearing on everyone's screen.

2. **Join announcement is structural, not dismissable.** The `✦` sparkle on a new peer's name isn't a notification you can miss — it's part of the footer that's always visible. Even if you somehow miss the sparkle, the name is there permanently.

3. **The peer list IS the security.** If you see a name you don't recognize, press N to leave. Instant. The transcript you've already shared is gone (peers don't persist transcripts after disconnection — they're only held in memory during the session).

4. **Display names are OS usernames by default.** Not configurable to prevent impersonation. If two laptops show "alice" in the footer, something is wrong and the user should leave.

5. **mDNS broadcasts your presence when in party mode.** You're visible to the whole LAN. This is a feature, not a bug — it means no one can hide.

### What this doesn't solve

- A determined attacker who modifies the client code can impersonate a name
- Passive mDNS snooping reveals that VoxTerm is running (not transcript content)
- Without encryption, transcripts are plaintext on the network

### Encryption plan (not in V1)

The crypto module exists (AES-256-GCM) but key exchange is the unsolved UX problem. Best candidate for party mode:

**Proximity-verified key exchange:**
1. Both devices exchange public keys during TCP handshake
2. Derive shared secret via ECDH
3. Display a 4-emoji confirmation on both screens: 🌊🎸🔥🌙
4. Users glance at each other's screens — if emojis match, trust is established
5. Future connections between the same node_ids auto-trust (progressive trust)

This is the Signal model adapted for physical proximity. Zero typing, one glance. But it's V2.

---

## Proximity Resolution: The Campus Problem

### The scenario

University campus. 500 students on one WiFi network. 8 study groups using VoxTerm simultaneously in different rooms, floors, buildings. Alice presses N. Which of the 8 groups does she join?

### Why this matters

If auto-join picks the wrong group, the entire "one button, zero friction" promise breaks. Alice joins a stranger's group, sees unfamiliar transcripts, has to leave and try again. That's worse than a picker — it's a surprise failure.

### Proximity signals (ranked by reliability)

**1. Audio correlation (highest accuracy, V2)**

The killer insight: VoxTerm is already capturing audio. Two devices in the same room hear the same ambient sound — HVAC hum, background chatter, door closing. Compare short audio fingerprints between peers during the discovery phase.

Implementation:
- Compute a compact audio fingerprint (chromagram or MFCC hash) of the last 3 seconds of ambient audio
- Include fingerprint hash in mDNS TXT record (rotated every 5s)
- During resolution, compare fingerprints: high correlation = same room
- Threshold: require >0.7 correlation to auto-join, otherwise show picker

This is how Google Nearby Connections works. We have the advantage of already having a live audio stream.

**Tradeoffs:**
- (+) Most accurate physical proximity signal available
- (+) Works across subnets, VPNs, complex network topologies
- (-) Privacy: sharing audio fingerprints reveals ambient environment
- (-) Implementation complexity: needs robust fingerprinting that works with different mics, noise floors, and sample timing
- (-) ~3 second latency to accumulate enough audio for correlation

**2. mDNS response timing (moderate accuracy, V1)**

Devices on the same WiFi access point respond to mDNS queries faster. Measure time-to-first-response for each discovered peer. Cluster by latency — the tightest cluster is your local group.

Implementation:
- During scanning phase, measure mDNS response times for each peer
- Group peers by latency similarity (within 5ms = likely same AP)
- If one cluster contains a group, auto-join it
- If ambiguous, show picker

**Tradeoffs:**
- (+) Uses existing mDNS infrastructure
- (+) No privacy implications
- (-) Unreliable on mesh networks, enterprise WiFi with fast backhaul
- (-) Network latency doesn't map perfectly to physical distance

**3. Group size heuristic (low accuracy, V1)**

If multiple groups exist, auto-join the largest one. Reasoning: in a campus setting, most people are in the main group. Smaller groups are breakouts.

**Tradeoffs:**
- (+) Zero implementation complexity
- (-) Wrong when there are two roughly equal groups
- (-) New groups (1 person) never get auto-joined

**4. Recency heuristic (low accuracy, V1)**

Auto-join the most recently created group. Reasoning: if you just pressed N, the group created closest to your time is most likely the one in your physical context.

**Tradeoffs:**
- (+) Simple
- (-) Very unreliable

### V1 recommendation

**Use a combined heuristic with picker fallback:**

1. If exactly 1 group on the network → auto-join (no ambiguity)
2. If multiple groups, and one has significantly more peers (>2x the next) → auto-join the largest (it's the main event)
3. If multiple groups of similar size → show the one-line picker
4. If no groups → become host

**V2: Add audio correlation.** This is the real solution. It makes auto-join reliable even on campus WiFi with dozens of groups. Audio correlation is VoxTerm's unique advantage — no other P2P app has a live audio stream to correlate with.

---

## The Dopamine Architecture

This section is about the *feeling* of using party mode. Not the protocol, not the state machine — the micro-rewards that make you want to press N.

### Principle: reward social action

Every social action should produce a small, pleasant feedback:
- Pressing N → immediate visual mode shift (the app "wakes up" socially)
- Finding peers → the count appearing feels like discovering something
- Joining → the brief glow/flash says "you made it"
- First peer transcript → the quality improvement is the ultimate reward
- Someone joining you → "people are coming to your party" is inherently rewarding

### Principle: never punish exploration

- Pressing N when no one is around → you become the host. Not "no groups found." You're now the party. Reframe absence as agency.
- Pressing N then immediately pressing N → clean exit. No "are you sure?" No data loss. No guilt.
- Being in a party alone for 5 minutes → no nagging. No "still waiting..." messages. Just quiet readiness. When someone joins, it'll feel like a reward precisely because you weren't being nagged.

### Specific micro-interactions

**The `2 nearby` count in solo mode:**
This is a passive nudge. It says "people are here" without saying "you should join." It creates FOMO without pressure. The count updates live as peers appear/disappear. Watching it change is mildly interesting — "oh, someone else just started VoxTerm."

**The scanning pulse:**
The `◌` indicator should breathe — slow pulse, not a fast spin. Like a heartbeat, not a loading bar. This communicates "I'm alive and searching" not "please wait."

**The join glow:**
When you successfully join a party, the footer's border color should briefly brighten (the `heavy #00e5ff` border could flash to `#00ffcc` for 500ms, then fade back). This is the "door opens" moment. Subtle but unmistakable.

**The peer arrival sparkle:**
When a new peer appears in the footer, their dot gets a `✦` next to it for 3 seconds. Not animated. Just present, then gone. It says "someone just arrived" without screaming it.

**The first peer transcript annotation:**
```
── via alice ──
```
One line. Centered. Dim but visible. It marks the moment when the party pays off — from now on, your transcripts are better because alice is here. This is the "aha" moment. After this annotation, peer transcripts blend in seamlessly. The quality is the reward.

---

## Edge Cases

### Two people press N simultaneously

Both devices scan, both find zero groups, both create groups. Now there are two groups of 1.

**Solution:** During the scanning phase (0.5–3s), if a new group appears that was created within the same window, merge. Specifically: if you just created a group (0 peers) and discover another group that was also just created (0 peers), the device with the lower node_id dissolves its group and joins the other. This happens automatically during the scanning → resolution transition.

### WiFi drops mid-party

Peer disappears from mDNS. TCP connection breaks. Heartbeat timeout fires.

**Solution:** The peer's dot in the footer dims (doesn't disappear immediately). After 10 seconds, it disappears. If the peer reconnects within 10 seconds, the dot re-brightens. No announcement, no disruption. Networks are flaky — the UI should absorb jitter, not amplify it.

### Someone joins the wrong group

Alice auto-joins bob's group but wanted carol's group.

**Solution:** Press N to leave (instant). Press N again — this time, carol's group is visible and the picker appears (because now there are multiple groups). Select carol's group, ENTER.

This is a 3-keystroke correction: N (leave), N (scan, picker appears), ENTER (join). Acceptable because it's rare.

### A stranger joins

Unknown name appears in the footer.

**Solution:** The anti-spy design handles this — the name is visible, you can press N to leave. Future: session pinning (lock the party after expected members join) and kick functionality.

### 100 VoxTerm instances on campus WiFi

mDNS discovery returns 100 peers across 15 groups.

**Solution:** The picker shows at most 5 groups (sorted by size or proximity). The rest are accessible by scrolling. But with audio correlation (V2), auto-join should work even here.

### You're the only one

Press N, no one else has VoxTerm open.

**Solution:** You become the host. Footer shows: `● you ✦  [N]`. No "waiting for others" — that's needy. Just your name, a sparkle (you just joined your own party), and the exit key. When someone eventually presses N, they'll find your party and join.

---

## Implementation Plan

### What changes from iteration 6

| Component | Iteration 6 (Network Bar) | Iteration 7 (Party Mode) |
|-----------|--------------------------|--------------------------|
| N key behavior | Opens a browse/info bar | Toggles party mode on/off |
| Joining | Browse groups → select → ENTER | Auto-join, picker only if ambiguous |
| Creating | Explicit C key in bar | Automatic if no groups found |
| Footer UI | Separate NetworkBar widget | Integrated into telemetry footer |
| User input needed | 2–3 keystrokes minimum | 1 keystroke (N), picker only if needed |
| Feel | Configuration tool | Social action |

### Files to modify

| File | Change |
|------|--------|
| `app.py` | Replace `action_new_session`/`action_join_session` with `action_toggle_party`. Remove N/J dual binding. Add party state machine. |
| `widgets/network_bar.py` | Repurpose as inline picker (only shown during multi-group resolution). Simplify to just the picker, remove browse/info modes. |
| `widgets/room_invite.py` | Remove. Toast model is dead. |
| `widgets/peer_browser.py` | Remove SessionCreateScreen/SessionJoinScreen modals. No more code input. |
| `network/discovery.py` | Add `group_name` to mDNS TXT (already done). Add passive peer count for `2 nearby` display. Consider audio fingerprint field (V2). |
| `network/session.py` | Auto-derive session key from group identity instead of user-provided code. |
| `config.py` | Add party mode constants (scan timeout, glow duration, etc.) |
| `cyberpunk.tcss` | Footer glow animation, scanning pulse, peer sparkle |

### New app.py state machine

```python
class PartyState(Enum):
    SOLO = "solo"           # not in party mode
    SCANNING = "scanning"   # pressed N, looking for groups
    PICKING = "picking"     # multiple groups, user choosing  
    JOINING = "joining"     # connecting to a group
    HOSTING = "hosting"     # created group, waiting for peers
    IN_PARTY = "in_party"  # connected, transcripts flowing

# N key handler:
def action_toggle_party(self):
    if self._party_state == PartyState.SOLO:
        self._enter_party_mode()    # → SCANNING
    else:
        self._leave_party_mode()    # → SOLO

# State transitions (automatic):
# SCANNING → found 1 group → JOINING → IN_PARTY
# SCANNING → found 0 groups → HOSTING (→ IN_PARTY when peer joins)
# SCANNING → found N groups → PICKING → user selects → JOINING → IN_PARTY
# SCANNING → timeout (3s) → HOSTING
```

### Session key derivation without codes

Current: user types a code → `derive_session_key(code)` → AES key.

Party mode: no codes. Instead:
1. Group creator generates a random session token on creation
2. Token is included in the TCP HELLO handshake (over plaintext in V1)
3. Joiners receive the token and derive the same key
4. V2: ECDH key exchange replaces token sharing

For V1 (plaintext), this is a no-op — no encryption. But the architecture supports adding it without changing the join flow.

---

## Language Guide

Words matter. The copy in the UI shapes how the feature *feels*.

### Use

| Context | Text |
|---------|------|
| Solo footer | `[N] Party` |
| Passive peer detection | `2 nearby` |
| Scanning | `◌ looking for the party...` |
| Hosting (alone) | `● you ✦` |
| In party | `● alice  ● bob  ● you` |
| Peer arrival | `● carol ✦` (sparkle, 3s) |
| Multi-group picker | `▸ alice's (3)  bob's (2)` |
| First peer transcript | `── via alice ──` |

### Don't use

| Avoid | Why | Instead |
|-------|-----|---------|
| "session" | Technical, cold | "party" |
| "group" (in UI copy) | Corporate, formal | "party" (internal code can use "group") |
| "connecting..." | Makes the user think about networks | "joining alice's party..." |
| "waiting for peers" | Needy, lonely | (nothing — just show your name) |
| "no groups found" | Failure framing | "you're the party now" (host) |
| "scanning network" | Technical | "looking for the party" |
| "disconnected" | Error framing | (name just disappears from footer) |
| "peer" (in UI) | Technical | Use the person's name |
| "[L]eave" | Requires learning a new key | N to leave (same key as join) |

---

## Open Questions

1. **Should there be an audio cue on join?** A subtle sound (like Discord's join chime) would make the arrival moment more tangible. But it breaks the silent, ambient nature of the tool. Leaning no for V1.

2. **Should party mode persist across app restarts?** If you close and reopen VoxTerm, should it auto-rejoin the party? Leaning no — each launch is a fresh choice. But the `2 nearby` nudge re-appears immediately.

3. **Should the waveform change in party mode?** A subtle hue shift would reinforce the mode change. But it might be distracting. Need to test.

4. **How to handle name display when 5+ people are in the party?** Footer space is limited. Show first 4 names + `+2 more`? Scroll? Need to test with real groups.

5. **Should you be able to kick someone?** Ptacek says yes (session pinning). But it adds complexity to the "one button" model. Maybe a separate, discoverable key (K?) that only appears in the help modal.

6. **Audio correlation for proximity: how to handle privacy?** Sharing audio fingerprints reveals ambient environment. Even a hash leaks information (you can tell if two devices are in the same room). Is this acceptable? Probably yes, given that you're already sharing full transcripts once connected.

---

## Success Criteria

How we know party mode is working:

1. **The 2-second test.** New user, never seen VoxTerm before. Told "press N to share transcripts with people nearby." They press N. Within 2 seconds, they're in a party and seeing peer transcripts. They never typed a code, never selected from a list, never read a debug log.

2. **The hallway test.** Two VoxTerm users walk past each other in a hallway. Neither joins the other's party. Party mode doesn't fire accidentally — you have to press N. But both see `1 nearby` in their footer and know the other exists.

3. **The campus test.** 8 groups on the same WiFi. Alice presses N and joins the right group on the first try. (V1: because there was only 1 group nearby or the picker was obvious. V2: because audio correlation picked the right room.)

4. **The spy test.** Bob joins alice's party without her knowledge. Impossible — alice sees `bob ✦` appear in her footer the moment he connects. She presses N to leave if she doesn't recognize him.

5. **The magic test.** User has been in a party for 5 minutes. Asked: "what was the best moment?" They say something about the transcript quality, not the connection flow. The connection was invisible. The transcript is the product.

---

## Revision History

| Date | Change |
|------|--------|
| 2026-03-22 | Iterations 1-2: session codes (killed) |
| 2026-03-22 | Iteration 3: toast + auto-join (killed: privacy) |
| 2026-03-24 | Iteration 4: "go live" (killed: no boundary) |
| 2026-03-25 | Iteration 5: context-sensitive N (killed: unpredictable) |
| 2026-03-26 | Iteration 6: network bar (shipped, works, but feels like plumbing) |
| 2026-04-02 | Iteration 7: party mode (this document) |
