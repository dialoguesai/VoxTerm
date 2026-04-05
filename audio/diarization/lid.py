"""Language identification using 3D-Speaker models.

Detects the spoken language from audio segments. Currently supports
Mandarin (zh) and English (en) using CAM++ or ERes2Net LID models
from the 3D-Speaker project.

Can run via ONNX (no PyTorch, safe in main process) or via PyTorch
in the diarizer subprocess.

Requires: ONNX model exported via scripts/export_onnx.py --model campplus_lid
          OR speakerlab + torch for the PyTorch path.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from audio.diarization.fbank import compute_fbank

log = logging.getLogger(__name__)

# 3D-Speaker LID models on ModelScope
LID_MODELS = {
    "campplus_lid": {
        "modelscope_id": "iic/speech_campplus_lre_en-cn_16k",
        "onnx_filename": "campplus_lid.onnx",
        "languages": ["en", "zh"],
    },
    "eres2net_lid": {
        "modelscope_id": "iic/speech_eres2net_base_lre_en-cn_16k",
        "onnx_filename": "eres2net_lid.onnx",
        "languages": ["en", "zh"],
    },
}

# Mapping from model output index to language code
# (3D-Speaker LID models: 0=en, 1=zh — verify when exporting)
_DEFAULT_LANG_MAP = {0: "en", 1: "zh"}

CACHE_DIR = Path.home() / ".cache" / "3dspeaker"


class LanguageIdentifier:
    """Detect spoken language from audio using 3D-Speaker LID models.

    The model outputs per-language logits which are softmax-normalized
    into confidence scores.
    """

    def __init__(
        self,
        model_name: str = "campplus_lid",
        min_audio_sec: float = 3.0,
        cache_dir: Path | None = None,
    ):
        self.model_name = model_name
        self.min_audio_sec = min_audio_sec
        self._cache_dir = cache_dir or CACHE_DIR
        self._session = None
        self._loaded = False
        self._lang_map: dict[int, str] = {}

        if model_name not in LID_MODELS:
            raise ValueError(
                f"Unknown LID model '{model_name}'. "
                f"Available: {list(LID_MODELS.keys())}"
            )

    def load(self) -> None:
        """Load the ONNX LID model."""
        import onnxruntime

        config = LID_MODELS[self.model_name]
        model_path = self._cache_dir / self.model_name / config["onnx_filename"]

        if not model_path.exists():
            log.warning(
                "LID ONNX model not found at %s. "
                "Run: python -m diarization.export_onnx --model %s",
                model_path, self.model_name,
            )
            return

        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1

        self._session = onnxruntime.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
            sess_options=opts,
        )

        # Build language map from config
        languages = config["languages"]
        self._lang_map = {i: lang for i, lang in enumerate(languages)}
        self._loaded = True
        log.info("Loaded LID model: %s (languages: %s)", self.model_name, languages)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def supported_languages(self) -> list[str]:
        """Return list of language codes this model can detect."""
        config = LID_MODELS.get(self.model_name, {})
        return config.get("languages", [])

    def identify(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
    ) -> tuple[str, float] | None:
        """Identify the language of an audio segment.

        Args:
            audio: 1-D float32 array in [-1, 1].
            sample_rate: Audio sample rate.

        Returns:
            (language_code, confidence) tuple, or None if audio too short
            or model not loaded. Confidence is in [0, 1].
        """
        if not self._loaded or self._session is None:
            return None

        audio = np.asarray(audio, dtype=np.float32).ravel()
        min_samples = int(self.min_audio_sec * sample_rate)
        if len(audio) < min_samples:
            return None

        # Compute features
        feats = compute_fbank(audio, sample_rate=sample_rate)
        if feats.shape[0] == 0:
            return None

        # Run inference
        feats_input = feats[np.newaxis, :, :].astype(np.float32)
        outputs = self._session.run(None, {"feature": feats_input})
        logits = outputs[0].squeeze()  # (num_languages,)

        # Softmax to get probabilities
        exp_logits = np.exp(logits - logits.max())
        probs = exp_logits / exp_logits.sum()

        best_idx = int(np.argmax(probs))
        confidence = float(probs[best_idx])
        lang_code = self._lang_map.get(best_idx, "unknown")

        return lang_code, confidence

    def identify_with_scores(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
    ) -> dict[str, float] | None:
        """Identify language and return all per-language scores.

        Returns dict mapping language codes to confidence scores,
        or None if audio is too short or model not loaded.
        """
        if not self._loaded or self._session is None:
            return None

        audio = np.asarray(audio, dtype=np.float32).ravel()
        min_samples = int(self.min_audio_sec * sample_rate)
        if len(audio) < min_samples:
            return None

        feats = compute_fbank(audio, sample_rate=sample_rate)
        if feats.shape[0] == 0:
            return None

        feats_input = feats[np.newaxis, :, :].astype(np.float32)
        outputs = self._session.run(None, {"feature": feats_input})
        logits = outputs[0].squeeze()

        exp_logits = np.exp(logits - logits.max())
        probs = exp_logits / exp_logits.sum()

        return {
            self._lang_map.get(i, f"lang_{i}"): float(p)
            for i, p in enumerate(probs)
        }
