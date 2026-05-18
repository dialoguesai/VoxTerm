"""Built-in prompt templates for transcript summarization.

Tuned for SMALL local models (0.6B–7B via MLX/Ollama), which ramble,
echo the transcript, or copy few-shot examples unless heavily constrained.
Techniques here are drawn from open-source peers (Hyprnote's anti-preamble
+ ASR-caveat + no-generic-sections rules; Screenpipe's exact-format
skeletons and hard caps; Vibe's triple-quote transcript fencing; Amurex's
anti-vagueness clause; Obsidian-AI-Notes' empty-section discipline).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Template:
    id: str
    label: str
    description: str
    system: str
    user: str  # uses {transcript} (and, for custom, {custom}) placeholders


# System prompt: identity + ASR caveat (a single line — correction is NOT a
# sub-task) + the anti-preamble / anti-echo / structure rules small models
# need. Kept terse on purpose; long system prompts dilute compliance on
# sub-3B models.
_SYSTEM = (
    "You are a precise meeting-notes assistant. Your input is a transcript "
    "produced by speech recognition: it may contain word errors, merged or "
    "mis-attributed speakers, timestamps, and speaker labels like [Alice] "
    "or 'Speaker 1'. Infer intended meaning from context, but never assert "
    "facts the transcript does not support and never invent names, numbers, "
    "or outcomes.\n\n"
    "Output rules — follow exactly:\n"
    "- Output ONLY the requested summary in markdown. No preamble, no "
    "commentary, no meta-discussion. Never write things like \"Here is the "
    "summary\" or \"I analyzed the transcript\".\n"
    "- Do not echo or re-transcribe the input. Synthesize; quote only where "
    "a section explicitly asks for quotes.\n"
    "- Use only `##` headings; never nest bullets more than one level.\n"
    "- Refer to speakers by their given label; do not invent names for "
    "unlabeled speakers.\n"
    "- Do not add generic filler sections (Overview, Introduction, "
    "Participants, Conclusion) unless explicitly requested."
)


# Transcript is fenced in triple quotes and flagged as data, not
# instructions — measurably reduces prompt-injection-by-transcript and
# verbatim regurgitation on small models.
_TRANSCRIPT = (
    "\n\nThe text between triple quotes is the transcript. Summarize it; "
    'do not repeat it back.\n"""\n{transcript}\n"""'
)


TEMPLATES: tuple[Template, ...] = (
    Template(
        id="tldr",
        label="TL;DR",
        description="2-3 sentence summary",
        system=_SYSTEM,
        user=(
            "Write a 2-3 sentence TL;DR as plain prose: no headings, no "
            "bullets, no preamble. Do not exceed 60 words. If the "
            "transcript has no substantive content, output exactly: "
            "No substantive discussion to summarize." + _TRANSCRIPT
        ),
    ),
    Template(
        id="meeting_notes",
        label="Meeting Notes",
        description="Topics, decisions, action items",
        system=_SYSTEM,
        user=(
            "Summarize the transcript as meeting notes using EXACTLY these "
            "headings, in this order:\n\n"
            "## Topics Discussed\n## Decisions\n## Action Items\n"
            "## Open Questions\n\n"
            "Rules:\n"
            "- Keep every heading. If a section has no content, put a "
            "single bullet: - None\n"
            "- Each bullet states one specific fact, decision, or task — "
            "not a vague topic label.\n"
            "- In Action Items use: "
            "- [ ] <task> — <owner or \"unassigned\"> — <due if stated>\n"
            "- Attribute decisions and tasks to the speaker label when the "
            "transcript makes it clear." + _TRANSCRIPT
        ),
    ),
    Template(
        id="action_items",
        label="Action Items",
        description="Just the to-dos as a checklist",
        system=_SYSTEM,
        user=(
            "Extract only the action items as a markdown checklist, one per "
            "line, in this exact row format:\n"
            "- [ ] <task> — <owner or \"unassigned\"> — <due date if "
            "stated>\n\n"
            "Rules:\n"
            "- Include only concrete, actionable tasks. Exclude vague "
            "aspirations like \"improve the process\" or \"discuss later\".\n"
            "- Do not invent owners or deadlines that were not stated.\n"
            "- If there are no action items, output exactly: - [ ] (none)"
            + _TRANSCRIPT
        ),
    ),
    Template(
        id="key_points",
        label="Key Points",
        description="Bulleted highlights and notable quotes",
        system=_SYSTEM,
        user=(
            "Summarize the transcript as a SHORT markdown bullet list of "
            "the key points — the main ideas, decisions, and takeaways.\n\n"
            "Rules:\n"
            "- Synthesize and condense. Each bullet abstracts over multiple "
            "lines; combine related statements into one point.\n"
            "- Do NOT reproduce the transcript line by line. No timestamps. "
            "No \"Speaker N:\" prefixes.\n"
            "- 5-8 bullets maximum, regardless of transcript length. Fewer, "
            "denser bullets beat many redundant ones.\n"
            "- Each bullet is a complete, self-contained statement, not a "
            "quoted utterance.\n"
            "- Then add a `## Quotes` heading with 1-2 short quotes copied "
            "VERBATIM from the transcript, each attributed to its speaker "
            "label. Never put paraphrased text inside quotes.\n\n"
            "Format example — copy the STYLE, never this placeholder "
            "content:\n"
            "- <one synthesized idea drawn from several turns>\n"
            "- <a decision that was reached, and why>\n"
            "- <an open problem and who owns the follow-up>" + _TRANSCRIPT
        ),
    ),
    Template(
        id="custom",
        label="Custom prompt…",
        description="Type your own summarization instruction",
        system=_SYSTEM,
        user="{custom}" + _TRANSCRIPT,
    ),
)


_BY_ID = {t.id: t for t in TEMPLATES}


def resolve_template(template_id: str) -> Template:
    """Look up a template by id. Falls back to tldr if unknown."""
    return _BY_ID.get(template_id, _BY_ID["tldr"])
