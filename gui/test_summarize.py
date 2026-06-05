"""Tests for GUI local-LLM summarize wiring (summarizer backend mocked)."""

from gui.engine import Engine

STEM = "2026-01-01_000000"


def _write_transcript(tmp_path, stem=STEM, text="# T\n\n**[00:01]** **Speaker 1:** hello world"):
    (tmp_path / f"{stem}-transcript.md").write_text(text, encoding="utf-8")


def test_summarize_ok(monkeypatch, tmp_path):
    import summarizer

    class _FakeSum:
        def summarize(self, body, template, custom_prompt=""):
            return "SUMMARY of: " + body.splitlines()[-1]

    monkeypatch.setattr(summarizer, "get_summarizer", lambda model="": _FakeSum())
    monkeypatch.setattr(summarizer, "resolve_template", lambda tid: tid)
    _write_transcript(tmp_path)
    r = Engine(out_dir=tmp_path).summarize_session(STEM, template_id="tldr")
    assert r["ok"] and r["summary"].startswith("SUMMARY of:") and r["template"] == "tldr"


def test_summarize_no_transcript(tmp_path):
    r = Engine(out_dir=tmp_path).summarize_session(STEM)
    assert not r["ok"] and "not found" in r["error"]


def test_summarize_backend_unavailable_is_graceful(monkeypatch, tmp_path):
    import summarizer

    def _boom(model=""):
        raise summarizer.SummarizerError("MLX only on Apple Silicon — use ollama:<model>")

    monkeypatch.setattr(summarizer, "get_summarizer", _boom)
    monkeypatch.setattr(summarizer, "resolve_template", lambda tid: tid)
    _write_transcript(tmp_path)
    r = Engine(out_dir=tmp_path).summarize_session(STEM, model="")
    assert not r["ok"] and "ollama" in r["error"]   # the clear, instructive message is surfaced, not a crash


def test_summarize_rejects_path_traversal(tmp_path):
    r = Engine(out_dir=tmp_path).summarize_session("../etc/passwd")
    assert not r["ok"]   # read_artifact/_resolve refuses traversal -> "not found"
