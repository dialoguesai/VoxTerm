"""Built-in prompt templates for transcript summarization."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Template:
    id: str
    label: str
    description: str
    system: str
    user: str  # uses {transcript} placeholder


_SYSTEM = (
    "You are a careful note-taker. You summarize meeting and conversation "
    "transcripts that may contain speaker labels like [Alice], timestamps, "
    "and ASR errors. Be faithful to what was said. Do not invent facts. "
    "Keep speaker attributions when relevant. Write in plain markdown."
)


TEMPLATES: tuple[Template, ...] = (
    Template(
        id="tldr",
        label="TL;DR",
        description="2-3 sentence summary",
        system=_SYSTEM,
        user=(
            "Write a 2-3 sentence TL;DR of this transcript. No headings, "
            "no bullet points, just prose.\n\n---\n{transcript}"
        ),
    ),
    Template(
        id="meeting_notes",
        label="Meeting Notes",
        description="Topics, decisions, action items",
        system=_SYSTEM,
        user=(
            "Summarize this transcript as meeting notes with these sections:\n"
            "## Topics Discussed\n## Decisions\n## Action Items\n## Open Questions\n\n"
            "Omit any section that has no content. Attribute action items to "
            "the speaker when possible.\n\n---\n{transcript}"
        ),
    ),
    Template(
        id="action_items",
        label="Action Items",
        description="Just the to-dos as a checklist",
        system=_SYSTEM,
        user=(
            "Extract just the action items from this transcript as a markdown "
            "checklist. Use `- [ ] @speaker: action` format. If no action items "
            "were discussed, say so explicitly.\n\n---\n{transcript}"
        ),
    ),
    Template(
        id="key_points",
        label="Key Points",
        description="Bulleted highlights and notable quotes",
        system=_SYSTEM,
        user=(
            "Summarize this transcript as a SHORT markdown bullet list of "
            "the key points — the main ideas, decisions, and takeaways.\n\n"
            "Rules:\n"
            "- Synthesize and condense. Each bullet must abstract over "
            "multiple lines of the transcript; combine related statements "
            "into a single point.\n"
            "- Do NOT reproduce the transcript line by line. Do NOT include "
            "timestamps. Do NOT prefix bullets with speaker labels like "
            "'Speaker 1:'.\n"
            "- Aim for 5-10 bullets total regardless of transcript length. "
            "Fewer, denser bullets are better than many redundant ones.\n"
            "- Write each bullet as a complete, self-contained statement of "
            "an idea, not a quoted utterance.\n\n"
            "Example of the desired style (note: synthesized, no timestamps, "
            "no speaker prefixes):\n"
            "- The services can't reach each other because they're bound to "
            "a loopback (127.0.0.1) address instead of an external IP.\n"
            "- The VPS network configuration is unknown and needs "
            "investigation; this is non-blocking for the rest of the work.\n"
            "- Gas costs for the run still need to be tallied to estimate "
            "total spend.\n\n"
            "After the bullets, add a `## Quotes` heading with 1-2 short, "
            "notable verbatim quotes (with speaker attribution).\n\n"
            "---\n{transcript}"
        ),
    ),
    Template(
        id="custom",
        label="Custom prompt…",
        description="Type your own summarization instruction",
        system=_SYSTEM,
        user="{custom}\n\n---\n{transcript}",
    ),
)


_BY_ID = {t.id: t for t in TEMPLATES}


def resolve_template(template_id: str) -> Template:
    """Look up a template by id. Falls back to tldr if unknown."""
    return _BY_ID.get(template_id, _BY_ID["tldr"])
