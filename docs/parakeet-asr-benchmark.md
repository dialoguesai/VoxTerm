# NVIDIA Parakeet ASR integration & benchmark

## TL;DR

- Added a **`ParakeetTranscriber`** backend (`audio/transcriber.py`) that runs
  NVIDIA Parakeet FastConformer models on Apple Silicon via
  [`parakeet-mlx`](https://github.com/senstella/parakeet-mlx). Two models are
  wired into the registry:
  - **`parakeet-0.6b`** → `mlx-community/parakeet-tdt-0.6b-v3` (0.6B, multilingual)
  - **`parakeet-1.1b`** → `mlx-community/parakeet-tdt-1.1b` (1.1B, higher param count)
- **`parakeet-1.1b` is the standout**: tied-best accuracy with the largest Qwen3
  model while running **~12× faster** (RTF 0.018 vs 0.226).

## Why not the requested `nvidia/nemotron-speech-streaming-en-0.6b`?

The original request was to integrate `nvidia/nemotron-speech-streaming-en-0.6b`
and a higher-param sibling. That specific model **cannot run on this stack today**:

- It's a NeMo **cache-aware *streaming*** FastConformer-RNNT. NeMo inference is
  CUDA/Linux-only — there are no Apple-Silicon/Metal wheels.
- The community MLX conversion (`animaslabs/nemotron-speech-streaming-en-0.6b-mlx`)
  exists, but `parakeet-mlx` 0.5.1 **fails to load it**. Concretely:
  1. `encoder.att_context_size` is the multi-latency list-of-lists
     `[[70,13],[70,6],[70,1],[70,0]]`; the loader expects a flat `list[int]`.
  2. After flattening that, the encoder uses `causal_downsampling: true` +
     `conv_context_size: "causal"` + batch-norm convs — none of which
     `parakeet-mlx` implements (`NotImplementedError: Other subsampling…`).
  3. Forcing the non-causal path loads the wrong layer set (48 missing
     batch-norm params) and crashes on a subsampling shape mismatch
     (`addmm` 4096 vs 4352).

Running it would mean writing new MLX layers (causal DwStriding subsampling,
causal convs, streaming cache) — out of scope, and the project already has
strong non-streaming ASR. So we integrated the **supported non-streaming
sibling** (`parakeet-tdt-0.6b-v3`, same FastConformer family) plus the
**higher-param `parakeet-tdt-1.1b`**, and benchmarked both.

## Results

12 macOS-`say` TTS clips, 42.4s total audio, M-series Metal GPU. WER normalised
(lowercased, punctuation stripped). Reproduce with `python -m dev.bench_asr`.

| model            | backend       | params | WER%  | RTF    | avg latency | load |
|------------------|---------------|--------|-------|--------|-------------|------|
| **parakeet-1.1b**| parakeet-mlx  | 1.1B   | 0.00  | 0.018  | 0.06s       | 0.2s |
| qwen3-1.7b       | qwen3-asr     | 1.7B   | 0.00  | 0.226  | 0.80s       | 0.7s |
| qwen3-0.6b       | qwen3-asr     | 0.6B   | 1.69  | 0.101  | 0.36s       | 0.4s |
| small (whisper)  | mlx-whisper   | 0.24B  | 1.69  | 0.033  | 0.12s       | 1.1s |
| parakeet-0.6b    | parakeet-mlx  | 0.6B   | 3.39  | 0.013  | 0.05s       | 0.9s |

`RTF` = processing time ÷ audio duration (lower is faster; 0.018 ≈ 55× real-time).

### Reading the numbers

- **Accuracy**: clean TTS is easy, so treat WER as a *relative* ranking, not an
  absolute. `parakeet-1.1b` and `qwen3-1.7b` were perfect. Most of the small WER
  gaps are **digit-vs-word formatting** ("9"/"6" vs "nine"/"six" from
  `parakeet-0.6b` and `whisper`), which the metric counts as errors but aren't
  real mistakes. `parakeet-0.6b`'s one genuine slip was "offline" → "a flying".
- **Speed**: both Parakeet models are dramatically faster than the current
  default Qwen3 family — `parakeet-1.1b` matches `qwen3-1.7b`'s accuracy at
  ~1/12th the compute, and `parakeet-0.6b` is the fastest model measured.
- **Output style**: `parakeet-tdt-0.6b-v3` emits punctuation + capitalization;
  `parakeet-tdt-1.1b` emits lowercase, no punctuation (older English model).

### Recommendation

`parakeet-1.1b` is a strong candidate to become a recommended (or default)
model on Apple Silicon: best-in-class accuracy here with by far the lowest
latency, which matters for the real-time pipeline.

## Caveats

- WER is on synthetic TTS audio. For an absolute comparison, re-run on
  LibriSpeech test-clean (or real session recordings) with reference transcripts.
- Parakeet language handling: the models infer language internally; the
  `language` arg is accepted for interface parity / the hallucination filter
  but isn't forwarded to the model.
