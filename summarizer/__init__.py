"""Local LLM summarization for VoxTerm transcripts."""

from .engine import Summarizer, SummarizerError, get_summarizer
from .prompts import TEMPLATES, Template, resolve_template

__all__ = [
    "Summarizer",
    "SummarizerError",
    "Template",
    "TEMPLATES",
    "get_summarizer",
    "resolve_template",
]
