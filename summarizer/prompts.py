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
            "List the key points from this transcript as a markdown bullet "
            "list. Include 1-2 notable verbatim quotes (with speaker "
            "attribution) under a `## Quotes` heading at the end.\n\n"
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
