"""Transcription engine — Qwen3-ASR (all platforms), mlx-whisper (macOS), faster-whisper (Linux fallback), llama server (remote)."""

from __future__ import annotations

import base64
import io
import json
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

    def reset_dedup(self):
        """Clear dedup state (public API for cross-model orchestration)."""
        self._recent.clear()

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

    def __init__(self, model: str = "mlx-community/whisper-small-mlx", language: str | None = "en"):
        self.model = model
        self._language = language
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
        if _is_hallucination(text, self._language):
            return {"text": "", "speaker": "", "speaker_id": 0}

        if self._is_duplicate(text):
            return {"text": "", "speaker": "", "speaker_id": 0}

        return {"text": text, "speaker": "", "speaker_id": 0}

    @property
    def is_loaded(self) -> bool:
        return self._loaded


_ALLOWED_MODEL_TYPES = {"qwen3", "whisper"}


class CrossModelTranscriber(_DeduplicatorMixin):
    """Cross-model validation transcriber — runs multiple models and uses
    majority voting at the word level to produce the best output.

    Inspired by SynthVision's cross-model validation approach and ROVER
    (Recognizer Output Voting Error Reduction). Models with different
    architectures have complementary error patterns — word-level voting
    lets the majority correct individual model mistakes.

    Supports 2 or 3 models. With 3 models, true majority voting is possible.
    """

    def __init__(
        self,
        primary_model: str = "Qwen/Qwen3-ASR-0.6B",
        secondary_model: str = "mlx-community/whisper-small-mlx",
        primary_type: str = "qwen3",
        secondary_type: str = "whisper",
        tertiary_model: str | None = None,
        tertiary_type: str | None = None,
        language: str | None = "en",
    ):
        self._models = []
        for model_id, model_type, role in [
            (primary_model, primary_type, "primary_type"),
            (secondary_model, secondary_type, "secondary_type"),
        ]:
            if model_type not in _ALLOWED_MODEL_TYPES:
                raise ValueError(
                    f"Unknown {role} '{model_type}'. Expected one of {sorted(_ALLOWED_MODEL_TYPES)}."
                )
            if model_type == "qwen3":
                self._models.append(Qwen3Transcriber(model=model_id, language=language))
            else:
                self._models.append(WhisperTranscriber(model=model_id, language=language))
        if tertiary_model and tertiary_type:
            if tertiary_type not in _ALLOWED_MODEL_TYPES:
                raise ValueError(
                    f"Unknown tertiary_type '{tertiary_type}'. Expected one of {sorted(_ALLOWED_MODEL_TYPES)}."
                )
            if tertiary_type == "qwen3":
                self._models.append(Qwen3Transcriber(model=tertiary_model, language=language))
            else:
                self._models.append(WhisperTranscriber(model=tertiary_model, language=language))

        self._loaded = False
        self._init_dedup()
        self._stats = {
            "all_agree": 0, "majority_vote": 0, "rescued": 0,
            "all_empty": 0, "fallback_primary": 0, "total": 0,
        }

    def load(self):
        """Load all models."""
        for m in self._models:
            m.load()
        self._loaded = True

    @staticmethod
    def _normalize(text: str) -> str:
        """Normalize text for comparison."""
        import re
        t = text.lower().strip()
        t = re.sub(r'[^\w\s]', '', t)
        t = re.sub(r'\s+', ' ', t).strip()
        return t

    @staticmethod
    def _align_pair(words_a: list[str], words_b: list[str]) -> list[tuple]:
        """Align two word sequences via edit distance. Returns aligned pairs."""
        n, m = len(words_a), len(words_b)
        dp = [[0] * (m + 1) for _ in range(n + 1)]
        for i in range(n + 1):
            dp[i][0] = i
        for j in range(m + 1):
            dp[0][j] = j
        for i in range(1, n + 1):
            for j in range(1, m + 1):
                if words_a[i - 1] == words_b[j - 1]:
                    dp[i][j] = dp[i - 1][j - 1]
                else:
                    dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])

        aligned = []
        i, j = n, m
        while i > 0 or j > 0:
            if i > 0 and j > 0 and words_a[i - 1] == words_b[j - 1]:
                aligned.append((words_a[i - 1], words_b[j - 1]))
                i -= 1
                j -= 1
            elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
                aligned.append((words_a[i - 1], words_b[j - 1]))
                i -= 1
                j -= 1
            elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
                aligned.append((words_a[i - 1], None))
                i -= 1
            elif j > 0:
                aligned.append((None, words_b[j - 1]))
                j -= 1
            else:
                if i > 0:
                    aligned.append((words_a[i - 1], None))
                    i -= 1

        aligned.reverse()
        return aligned

    def _rover_merge(self, texts: list[str]) -> tuple[str, bool]:
        """ROVER-style word-level voting across multiple transcriptions.

        Builds a confusion network by aligning each hypothesis to the primary
        backbone, then votes at each slot. Properly tracks word positions to
        avoid duplication bugs.

        Returns (merged_text, had_disagreement).
        """
        if not texts:
            return "", False
        if len(texts) == 1:
            return texts[0], False

        raw_words = [t.split() for t in texts]
        norm_words = [[self._normalize(w) for w in ws] for ws in raw_words]
        n_models = len(texts)

        # Build confusion network from pairwise alignments to backbone
        backbone_norm = norm_words[0]
        backbone_raw = raw_words[0]

        # Each slot: {normalized_word: (raw_word, count)}
        # Plus epsilon ("") for deletions
        network: list[dict[str, tuple[str, int]]] = []
        for nw, rw in zip(backbone_norm, backbone_raw):
            network.append({nw: (rw, 1)})

        # Map from original backbone positions to current network indices.
        # Insertions shift later slots, so we track the offset.
        backbone_to_net = list(range(len(backbone_norm)))
        # Track insertion slots: (backbone_pos, norm_word) → network index
        # so later models can vote into existing insertion slots.
        insertion_slots: dict[tuple[int, str], int] = {}

        for sec_idx in range(1, n_models):
            sec_norm = norm_words[sec_idx]
            sec_raw = raw_words[sec_idx]
            alignment = self._align_pair(backbone_norm, sec_norm)

            # Track consumed positions in secondary to get correct raw word
            sec_consumed = 0
            backbone_pos = 0
            insertions: list[tuple[int, int, str, str]] = []  # (backbone_pos, net_index, norm, raw)

            for a_word, b_word in alignment:
                if a_word is not None and b_word is not None:
                    # Both aligned — vote into the correct network slot
                    net_idx = backbone_to_net[backbone_pos]
                    b_raw_word = sec_raw[sec_consumed] if sec_consumed < len(sec_raw) else b_word
                    slot = network[net_idx]
                    if b_word in slot:
                        old_raw, old_cnt = slot[b_word]
                        slot[b_word] = (old_raw, old_cnt + 1)
                    else:
                        slot[b_word] = (b_raw_word, 1)
                    backbone_pos += 1
                    sec_consumed += 1
                elif a_word is not None:
                    # Backbone has word, secondary doesn't — epsilon vote
                    net_idx = backbone_to_net[backbone_pos]
                    slot = network[net_idx]
                    if "" in slot:
                        old_raw, old_cnt = slot[""]
                        slot[""] = ("", old_cnt + 1)
                    else:
                        slot[""] = ("", 1)
                    backbone_pos += 1
                else:
                    # Secondary has extra word — check if a prior model already
                    # inserted this word at the same backbone position.
                    b_raw_word = sec_raw[sec_consumed] if sec_consumed < len(sec_raw) else b_word
                    ins_key = (backbone_pos, b_word)
                    if ins_key in insertion_slots:
                        # Vote into existing insertion slot
                        existing_idx = insertion_slots[ins_key]
                        slot = network[existing_idx]
                        if b_word in slot:
                            old_raw, old_cnt = slot[b_word]
                            slot[b_word] = (old_raw, old_cnt + 1)
                        else:
                            slot[b_word] = (b_raw_word, 1)
                        # Reduce epsilon count since this model votes for the word
                        if "" in slot:
                            eps_raw, eps_cnt = slot[""]
                            if eps_cnt > 1:
                                slot[""] = ("", eps_cnt - 1)
                            else:
                                del slot[""]
                    else:
                        # Record new insertion
                        net_idx = backbone_to_net[backbone_pos] if backbone_pos < len(backbone_to_net) else len(network)
                        insertions.append((backbone_pos, net_idx, b_word, b_raw_word))
                    sec_consumed += 1

            # Apply insertions in reverse order so earlier positions stay valid
            for bp, pos, ins_norm, ins_raw in reversed(insertions):
                new_slot = {"": ("", n_models - 1), ins_norm: (ins_raw, 1)}
                network.insert(pos, new_slot)
                # Record this insertion slot for later models
                insertion_slots[(bp, ins_norm)] = pos
                # Shift backbone_to_net: all mappings at or after pos move forward by 1
                for k in range(len(backbone_to_net)):
                    if backbone_to_net[k] >= pos:
                        backbone_to_net[k] += 1
                # Shift existing insertion_slots that are at or after pos
                for key, idx in insertion_slots.items():
                    if key != (bp, ins_norm) and idx >= pos:
                        insertion_slots[key] = idx + 1

        # Vote: pick highest-count candidate at each slot, skip epsilon winners
        merged = []
        had_disagreement = False
        for slot in network:
            if len(slot) > 1:
                had_disagreement = True
            # Pick winner: highest count, break ties by preferring non-empty
            best_word = ""
            best_raw = ""
            best_count = 0
            for word, (raw, count) in slot.items():
                if count > best_count or (count == best_count and word and not best_word):
                    best_word = word
                    best_raw = raw
                    best_count = count
            if best_word:  # Skip epsilon winners
                merged.append(best_raw)

        return " ".join(merged), had_disagreement

    def transcribe(self, audio: np.ndarray, **kwargs) -> dict:
        """Transcribe using cross-model voting."""
        self._stats["total"] += 1

        # Run all models
        results = []
        for m in self._models:
            m.reset_dedup()  # Reset dedup per model per sample
            r = m.transcribe(audio, **kwargs)
            results.append(r.get("text", "").strip())

        non_empty = [t for t in results if t]

        # All empty → noise
        if not non_empty:
            self._stats["all_empty"] += 1
            return {"text": "", "speaker": "", "speaker_id": 0}

        # Only one model produced text → rescue
        if len(non_empty) == 1:
            self._stats["rescued"] += 1
            text = non_empty[0]
        elif len(non_empty) >= 2:
            text, had_disagreement = self._rover_merge(non_empty)
            if had_disagreement:
                self._stats["majority_vote"] += 1
            else:
                self._stats["all_agree"] += 1
        else:
            self._stats["fallback_primary"] += 1
            text = results[0]

        if self._is_duplicate(text):
            return {"text": "", "speaker": "", "speaker_id": 0}

        return {"text": text, "speaker": "", "speaker_id": 0}

    @property
    def stats(self) -> dict:
        return dict(self._stats)

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


def discover_llama_audio_models(server_url: str) -> list[str] | None:
    """Query a llama.cpp or Ollama server for models that support audio input.

    Returns:
        list[str] — model names with audio capabilities (may be empty)
        None — if the server is unreachable
    """
    url = server_url.rstrip("/")

    # First check if server has /api/tags (Ollama-compatible listing)
    try:
        req = urllib.request.Request(f"{url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
    except Exception:
        # Server unreachable — try /health as fallback
        try:
            req = urllib.request.Request(f"{url}/health", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                # Server is up but has no /api/tags (plain llama.cpp)
                return []
        except Exception:
            return None

    # If server responded with model capabilities directly (llama.cpp style),
    # check for "multimodal" capability
    models = data.get("models", []) or data.get("data", [])
    audio_models = []
    # Cap probing to first 20 models to avoid slow startup
    for m in models[:20]:
        # llama.cpp /api/tags includes capabilities in the response
        caps = m.get("capabilities", [])
        if "multimodal" in caps:
            name = m.get("id") or m.get("name", "")
            if name:
                audio_models.append(name)
            continue

        # Ollama style: need to probe /api/show per model
        name = m.get("name", "")
        if not name:
            continue
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
    """Transcriber that delegates to a llama.cpp server via /v1/chat/completions.

    Requires a llama.cpp server running with an audio-capable model
    (e.g. Qwen2.5-Omni). Uses the OpenAI-compatible input_audio content type.
    """

    def __init__(self, server_url: str = "http://localhost:8080",
                 model: str = "", language: str | None = "en"):
        self.server_url = server_url.rstrip("/")
        self.model = model
        self._language = language
        self._loaded = False
        self._init_dedup()

    def load(self):
        """Verify the llama.cpp server is reachable via /health."""
        try:
            req = urllib.request.Request(f"{self.server_url}/health", method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                pass
        except Exception as e:
            raise ConnectionError(f"Cannot reach llama server at {self.server_url}: {e}")
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

        try:
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
        except urllib.error.URLError as e:
            raise ConnectionError(f"Llama server request failed: {e}") from e

        text = ((result.get("choices") or [{}])[0]
                .get("message", {}).get("content", "")).strip()

        if _is_hallucination(text, self._language):
            return {"text": "", "speaker": "", "speaker_id": 0}

        if self._is_duplicate(text):
            return {"text": "", "speaker": "", "speaker_id": 0}

        return {"text": text, "speaker": "", "speaker_id": 0}

    @property
    def is_loaded(self) -> bool:
        return self._loaded
