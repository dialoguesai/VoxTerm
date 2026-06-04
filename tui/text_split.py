"""Proportional splitting of a transcript across diarization segments.

Pure stdlib, NO Textual / sounddevice / model imports — so headless consumers (the GUI's
``gui.transcribe``, tests) can use it without dragging in the whole TUI. ``tui.app`` keeps a
thin ``VoxTerm._split_text_by_segments`` staticmethod that delegates here, so the live TUI
path is unchanged.
"""
from __future__ import annotations


def split_text_by_segments(
    text: str,
    segments: list[tuple[str, int, int, int]],
) -> list[tuple[str, str, int]]:
    """Split transcribed ``text`` across ``segments`` proportionally by duration.

    ``segments`` is a list of ``(label, speaker_id, start_sample, end_sample)``.
    Returns a list of ``(text_portion, speaker_label, speaker_id)``.
    """
    words = text.split()
    if not words or not segments:
        return [(text, "", 0)]

    total_samples = sum(end - start for _, _, start, end in segments)
    if total_samples <= 0:
        return [(text, segments[0][0], segments[0][1])]

    result = []
    word_idx = 0
    for i, (label, sid, start, end) in enumerate(segments):
        duration_frac = (end - start) / total_samples
        if i == len(segments) - 1:
            n_words = len(words) - word_idx          # last segment gets the remaining words
        else:
            n_words = max(1, round(len(words) * duration_frac))

        seg_words = words[word_idx:word_idx + n_words]
        word_idx += n_words
        result.append((" ".join(seg_words), label, sid))

    return result
