"""Transcription engine — Qwen3-ASR (all platforms), mlx-whisper (macOS), faster-whisper (Linux fallback), llama server (remote)."""

from __future__ import annotations

import base64
import io
import json
import sys
import re
import struct
import urllib.request
import urllib.error

import numpy as np

from audio.platform import CURRENT_PLATFORM, Platform


class _DeduplicatorMixin:
    """Tracks recent outputs and suppresses consecutive duplicates."""

    def _init_dedup(self):
        self._recent: list[str] = []

    def _is_duplicate(self, text: str) -> bool:
        normalized = text.lower().strip().rstrip(".")
        if normalized in self._recent:
            return True
        self._recent.append(normalized)
        if len(self._recent) > 5:
            self._recent.pop(0)
        return False


def _is_hallucination(text: str, expected_language: str | None = "en") -> bool:
    """Detect common ASR hallucination patterns (shared by all transcribers)."""
    if not text:
        return False
    if len(text) < 2:
        return True

    # Reject non-Latin script when expecting a Latin-script language
    if expected_language and expected_language in (
        "en", "fr", "de", "es", "it", "pt", "nl", "tr",
    ):
        if re.search(r'[\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\u0400-\u04ff\u0600-\u06ff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]', text):
            return True

    words = text.lower().split()
    if len(words) > 80:
        return True

    if len(words) >= 8:
        from collections import Counter
        for n in range(2, min(11, len(words) // 2 + 1)):
            if len(words) < n * 2:
                continue
            ngrams = [" ".join(words[i:i+n]) for i in range(len(words) - n + 1)]
            counts = Counter(ngrams)
            top_count = counts.most_common(1)[0][1]
            if top_count >= 3 and top_count > len(ngrams) * 0.25:
                return True

    hallucination_patterns = [
        r"^\.+$",
        r"^(thanks? (for )?watching)",
        r"^(subscribe)",
        r"^(please subscribe)",
        r"^(music|applause|\[music\])",
        r"^(you)$",
        r"^(so)$",
        r"^(oh)$",
        r"^(bye\.?)$",
        r"^(thank you\.?)$",
        r"^so,?\s+let'?s\s+go\.?$",
        r"^let'?s\s+go\.?$",
        r"^one,?\s+two,?\s+three,?\s+four\.?$",
        r"^i'?m\s+going\s+to\s+go\s+ahead",
    ]
    text_lower = text.lower().strip()
    for pattern in hallucination_patterns:
        if re.match(pattern, text_lower):
            return True
    return False


# qwen-asr (PyTorch) expects full language names, not ISO codes
_ISO_TO_LANG = {
    "en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
    "de": "German", "fr": "French", "es": "Spanish", "pt": "Portuguese",
    "ru": "Russian", "ar": "Arabic", "hi": "Hindi", "it": "Italian",
    "tr": "Turkish", "nl": "Dutch", "id": "Indonesian", "th": "Thai",
    "vi": "Vietnamese", "ms": "Malay", "sv": "Swedish", "da": "Danish",
    "fi": "Finnish", "pl": "Polish", "cs": "Czech", "el": "Greek",
    "ro": "Romanian", "hu": "Hungarian", "fa": "Persian",
}


class Qwen3Transcriber(_DeduplicatorMixin):
    """Qwen3-ASR transcriber — MLX on macOS, qwen-asr (PyTorch) on Linux."""

    def __init__(self, model: str = "Qwen/Qwen3-ASR-0.6B", language: str | None = "en"):
        self.model_id = model
        self._language = language
        self._model = None
        self._loaded = False
        self._use_mlx = CURRENT_PLATFORM == Platform.MACOS
        self._init_dedup()

    def load(self):
        """Pre-load the model (downloads on first run)."""
        if self._use_mlx:
            from mlx_qwen3_asr import load_model
            model, _config = load_model(self.model_id)
            self._model = model
        else:
            from qwen_asr import Qwen3ASRModel
            import torch
            dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
            self._model = Qwen3ASRModel.from_pretrained(
                self.model_id,
                dtype=dtype,
                device_map=device,
                max_new_tokens=256,
            )
        self._loaded = True

    def transcribe(self, audio: np.ndarray, **kwargs) -> dict:
        """Transcribe audio array (float32, 16kHz mono).

        Returns:
            {"text": str, "speaker": str, "speaker_id": int}
        """
        rms = float(np.sqrt(np.mean(audio ** 2)))
        if rms < 0.005:
            return {"text": "", "speaker": "", "speaker_id": 0}

        if self._use_mlx:
            from mlx_qwen3_asr import transcribe
            result = transcribe(
                audio,
                model=self._model if self._model else self.model_id,
                language=self._language,
                verbose=False,
            )
            text = str(result.text).strip() if hasattr(result, 'text') else ""
        else:
            lang = _ISO_TO_LANG.get(self._language, self._language) if self._language else None
            results = self._model.transcribe(
                audio=(audio, 16000),
                language=lang,
            )
            text = results[0].text.strip() if results else ""

        if _is_hallucination(text, self._language):
            return {"text": "", "speaker": "", "speaker_id": 0}

        if self._is_duplicate(text):
            return {"text": "", "speaker": "", "speaker_id": 0}

        return {"text": text, "speaker": "", "speaker_id": 0}

    @property
    def is_loaded(self) -> bool:
        return self._loaded


class WhisperTranscriber(_DeduplicatorMixin):
    """Legacy mlx-whisper transcriber (fallback)."""

    def __init__(self, model: str = "mlx-community/whisper-small-mlx"):
        self.model = model
        self._loaded = False
        self._init_dedup()

    def load(self):
        import mlx_whisper
        silent = np.zeros(16000, dtype=np.float32)
        mlx_whisper.transcribe(silent, path_or_hf_repo=self.model, verbose=False)
        self._loaded = True

    def transcribe(self, audio: np.ndarray, **kwargs) -> dict:
        import mlx_whisper

        rms = float(np.sqrt(np.mean(audio ** 2)))
        if rms < 0.005:
            return {"text": "", "speaker": "", "speaker_id": 0}

        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self.model,
            verbose=False,
            condition_on_previous_text=False,
            no_speech_threshold=0.5,
            compression_ratio_threshold=2.0,
        )

        text = result.get("text", "").strip()
        if _is_hallucination(text):
            return {"text": "", "speaker": "", "speaker_id": 0}

        if self._is_duplicate(text):
            return {"text": "", "speaker": "", "speaker_id": 0}

        return {"text": text, "speaker": "", "speaker_id": 0}

    @property
    def is_loaded(self) -> bool:
        return self._loaded


class FasterWhisperTranscriber(_DeduplicatorMixin):
    """Cross-platform transcriber using faster-whisper (CTranslate2 backend).

    Works on Linux (CPU/CUDA) and any platform with faster-whisper installed.
    """

    def __init__(self, model: str = "small", language: str | None = "en"):
        self.model_size = model
        self._language = language
        self._model = None
        self._loaded = False
        self._init_dedup()

    def load(self):
        """Pre-load the model (downloads on first run)."""
        from faster_whisper import WhisperModel
        self._model = WhisperModel(
            self.model_size, device="auto", compute_type="auto",
        )
        self._loaded = True

    def transcribe(self, audio: np.ndarray, **kwargs) -> dict:
        rms = float(np.sqrt(np.mean(audio ** 2)))
        if rms < 0.005:
            return {"text": "", "speaker": "", "speaker_id": 0}

        segments, _info = self._model.transcribe(
            audio,
            language=self._language,
            beam_size=5,
            vad_filter=False,  # we already run Silero VAD upstream
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()

        if _is_hallucination(text, self._language):
            return {"text": "", "speaker": "", "speaker_id": 0}

        if self._is_duplicate(text):
            return {"text": "", "speaker": "", "speaker_id": 0}

        return {"text": text, "speaker": "", "speaker_id": 0}

    @property
    def is_loaded(self) -> bool:
        return self._loaded


def _audio_to_wav_base64(audio: np.ndarray, sample_rate: int = 16000) -> str:
    """Encode float32 audio array as base64 WAV string."""
    audio_int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    num_samples = len(audio_int16)
    data_size = num_samples * 2
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(audio_int16.tobytes())
    return base64.b64encode(buf.getvalue()).decode("ascii")


def discover_llama_audio_models(server_url: str) -> list[str]:
    """Query an Ollama server for models that support audio input.

    Returns a list of model names that advertise audio capabilities,
    or an empty list if the server is unreachable / has none.
    """
    url = server_url.rstrip("/")
    try:
        req = urllib.request.Request(f"{url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
    except Exception:
        return []

    models = data.get("models", [])
    audio_models = []
    for m in models:
        name = m.get("name", "")
        try:
            show_req = urllib.request.Request(
                f"{url}/api/show",
                data=json.dumps({"name": name}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(show_req, timeout=5) as resp:
                details = json.loads(resp.read())
            model_info = details.get("model_info", {})
            template = details.get("template", "")
            info_str = json.dumps(model_info).lower() + template.lower()
            if "audio" in info_str:
                audio_models.append(name)
        except Exception:
            continue

    return audio_models


class LlamaServerTranscriber(_DeduplicatorMixin):
    """Transcriber that delegates to an OpenAI-compatible or Ollama server with an audio-capable model.

    Supports two server types:
    - llama.cpp server: uses /v1/chat/completions with input_audio content type
    - Ollama: uses /api/chat with images field (when Ollama adds audio support)

    The server type is auto-detected on load() by probing endpoints.
    """

    def __init__(self, server_url: str = "http://localhost:8080",
                 model: str = "", language: str | None = "en"):
        self.server_url = server_url.rstrip("/")
        self.model = model
        self._language = language
        self._loaded = False
        self._server_type: str = ""  # "llamacpp" or "ollama"
        self._init_dedup()

    def _probe_server_type(self) -> str:
        """Auto-detect whether this is an Ollama or llama.cpp server."""
        # Check /api/tags — both Ollama and llama.cpp implement this.
        # llama.cpp's response includes "object":"list" and model "capabilities",
        # while Ollama's does not.
        try:
            req = urllib.request.Request(f"{self.server_url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                if data.get("object") == "list":
                    return "llamacpp"
                if "models" in data:
                    return "ollama"
        except Exception:
            pass
        # Try llama.cpp /health endpoint
        try:
            req = urllib.request.Request(f"{self.server_url}/health", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return "llamacpp"
        except Exception:
            pass
        return "llamacpp"

    def load(self):
        """Verify the server is reachable. For Ollama, also check model exists."""
        self._server_type = self._probe_server_type()

        if self._server_type == "ollama":
            try:
                req = urllib.request.Request(f"{self.server_url}/api/tags", method="GET")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
            except Exception as e:
                raise ConnectionError(f"Cannot reach server at {self.server_url}: {e}")

            model_names = [m.get("name", "") for m in data.get("models", [])]
            if self.model and self.model not in model_names:
                base = self.model.split(":")[0] if ":" in self.model else self.model
                matches = [n for n in model_names if n.startswith(base)]
                if not matches:
                    available = ", ".join(model_names[:10])
                    raise ValueError(
                        f"Model '{self.model}' not found on server. Available: {available}"
                    )
        else:
            # llama.cpp: just verify the server is up via /health
            try:
                req = urllib.request.Request(f"{self.server_url}/health", method="GET")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    pass
            except Exception as e:
                raise ConnectionError(f"Cannot reach server at {self.server_url}: {e}")

        self._loaded = True

    def transcribe(self, audio: np.ndarray, **kwargs) -> dict:
        rms = float(np.sqrt(np.mean(audio ** 2)))
        if rms < 0.005:
            return {"text": "", "speaker": "", "speaker_id": 0}

        wav_b64 = _audio_to_wav_base64(audio)

        lang_hint = ""
        if self._language:
            from config import AVAILABLE_LANGUAGES
            lang_name = AVAILABLE_LANGUAGES.get(self._language, self._language)
            lang_hint = f" The audio is in {lang_name}."

        prompt_text = f"Transcribe the following audio exactly as spoken. Output ONLY the transcription text, nothing else.{lang_hint}"

        if self._server_type == "ollama":
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt_text, "images": [wav_b64]}],
                "stream": False,
            }
            endpoint = f"{self.server_url}/api/chat"
            # Ollama response: {"message": {"content": "..."}}
            text_path = lambda r: r.get("message", {}).get("content", "")
        else:
            # OpenAI-compatible (llama.cpp server)
            payload = {
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "input_audio", "input_audio": {"data": wav_b64, "format": "wav"}},
                    ],
                }],
            }
            if self.model:
                payload["model"] = self.model
            endpoint = f"{self.server_url}/v1/chat/completions"
            # OpenAI response: {"choices": [{"message": {"content": "..."}}]}
            text_path = lambda r: (r.get("choices") or [{}])[0].get("message", {}).get("content", "")

        try:
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
        except Exception as e:
            return {"text": f"[server error: {e}]", "speaker": "", "speaker_id": 0}

        text = text_path(result).strip()

        if _is_hallucination(text, self._language):
            return {"text": "", "speaker": "", "speaker_id": 0}

        if self._is_duplicate(text):
            return {"text": "", "speaker": "", "speaker_id": 0}

        return {"text": text, "speaker": "", "speaker_id": 0}

    @property
    def is_loaded(self) -> bool:
        return self._loaded
