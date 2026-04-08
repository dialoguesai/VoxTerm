# VOXTERM Configuration

# Audio
SAMPLE_RATE = 16000
CHUNK_SIZE = 1024
CHANNELS = 1
DTYPE = "float32"

# Transcription — Qwen3-ASR (primary) + legacy Whisper models
DEFAULT_MODEL = "qwen3-0.6b"
AVAILABLE_MODELS = {
    "qwen3-0.6b":  "Qwen/Qwen3-ASR-0.6B",
    "qwen3-1.7b":  "Qwen/Qwen3-ASR-1.7B",
    "tiny":        "mlx-community/whisper-tiny",
    "small":       "mlx-community/whisper-small-mlx",
    "medium":      "mlx-community/whisper-medium-mlx",
    "large-v3":    "mlx-community/whisper-large-v3-mlx",
    "turbo":       "mlx-community/whisper-large-v3-turbo",
    "distil-v3":   "distil-whisper/distil-large-v3",
}
# Which model keys use Qwen3-ASR vs Whisper backend
QWEN3_MODELS = {"qwen3-0.6b", "qwen3-1.7b"}
WHISPER_MODEL = "mlx-community/whisper-small-mlx"  # legacy default

# Language forcing for Qwen3-ASR (None = auto-detect)
DEFAULT_LANGUAGE = "en"
AVAILABLE_LANGUAGES = {
    "en": "English",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "ru": "Russian",
    "ar": "Arabic",
    "hi": "Hindi",
    "it": "Italian",
    "pt": "Portuguese",
    "tr": "Turkish",
    "nl": "Dutch",
}
MAX_BUFFER_SECONDS = 3.0
MIN_BUFFER_SECONDS = 1.0
SILENCE_THRESHOLD = 0.012
SILENCE_TRIGGER_SECONDS = 0.3
VAD_THRESHOLD = 0.5           # Silero VAD speech probability threshold

# Session persistence
LIVE_DIR = __import__("pathlib").Path.home() / "Documents" / "voxterm" / ".live"

# System audio capture — compiled Swift helper cached here
BIN_DIR = __import__("pathlib").Path.home() / "Documents" / "voxterm" / ".bin"

# Diarizer subprocess
DIARIZER_TIMEOUT = 5.0        # seconds to wait for subprocess response
DIARIZER_MAX_RESTARTS = 3     # max restarts before falling back to in-process
DIARIZER_RESTART_WINDOW = 60  # seconds — restart counter resets after this

# Crash reporting
CRASH_LOG_MAX_COUNT = 50      # max crash logs to keep (rotated on startup)

# Waveform
WAVEFORM_FPS = 15
WAVEFORM_HEIGHT = 11

# Online diarization thresholds
MATCH_THRESHOLD = 0.35             # cosine sim above this → assign to existing speaker
NEW_SPEAKER_THRESHOLD = 0.30      # must be below this vs ALL centroids to create new speaker
CONTINUITY_BONUS = 0.0            # disabled — was causing speaker transitions to stick
CONFLICT_MARGIN = 0.05            # if top-2 within this → prefer more established speaker
MERGE_THRESHOLD = 0.50            # pairwise cosine sim above this → merge clusters
QUALITY_RMS_THRESHOLD = 0.003     # min RMS energy for quality-gated centroid update
MERGE_INTERVAL = 3                # check for cluster merges every N identify() calls
RECLUSTER_INTERVAL = 8            # spectral re-clustering every N identify() calls
RECLUSTER_MIN_SEGMENTS = 4        # min total segments before re-clustering kicks in
LOOP_PROB = 0.99                  # VBx-style HMM self-transition probability
WHITEN_MIN_SEGMENTS = 8           # min segments before PLDA-lite whitening kicks in
SCD_CHANGE_THRESHOLD = 0.6        # cosine distance above this → speaker change detected
SCD_WINDOW_SEC = 2.0              # sliding window duration for SCD embedding extraction
SCD_HOP_SEC = 0.5                 # hop between consecutive SCD windows

# Cross-session speaker matching thresholds
CROSS_SESSION_HIGH_BASE = 0.55    # base threshold for auto-assign
CROSS_SESSION_MEDIUM = 0.35       # below this → unknown
ADAPTIVE_BOOST = 0.15             # extra strictness for new profiles
ADAPTIVE_DECAY_RATE = 10          # how fast the boost decays with samples
COLD_START_MIN_CONFIRMED = 10     # min confirmed before auto-updates allowed

# Colors
BG_COLOR = "#0a0e14"
BORDER_COLOR = "#00e5ff"
ACCENT_COLOR = "#00ffcc"
TEXT_COLOR = "#c0c0c0"
DIM_COLOR = "#004040"
BRIGHT_COLOR = "#00ffcc"
WARN_COLOR = "#ff6600"
ERROR_COLOR = "#ff0040"
ACTIVE_COLOR = "#00ff88"

# Block characters for waveform (high to low intensity)
WAVE_BLOCKS = ["█", "▓", "▒", "░", "·"]
