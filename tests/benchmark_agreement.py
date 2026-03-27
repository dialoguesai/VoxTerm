#!/usr/bin/env python3
"""Benchmark: Fire-and-Forget vs LocalAgreement transcription pipeline.

Simulates both approaches on the same audio file, measuring:
- Transcription latency per chunk
- Total wall time
- Word-level output comparison
- Number of transcribe() calls
- Buffer behavior (clear vs sliding window)

Usage:
    python3 tests/benchmark_agreement.py [--model qwen3-0.6b] [--audio tests/fixtures/speakers/dev00.wav]
"""

import argparse
import json
import os
import sys
import time

import numpy as np
import soundfile as sf

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import SAMPLE_RATE, AGREEMENT_TICK_SECONDS, AGREEMENT_MIN_AUDIO, AGREEMENT_FLUSH_SILENCE
from transcriber.agreement import AgreementState
from audio.buffer import AudioBuffer


def load_audio(path: str) -> np.ndarray:
    """Load audio file and resample to 16kHz mono float32."""
    data, sr = sf.read(path, dtype="float32")
    if len(data.shape) > 1:
        data = data.mean(axis=1)
    if sr != SAMPLE_RATE:
        # Simple resample via scipy
        from scipy.signal import resample
        n_samples = int(len(data) * SAMPLE_RATE / sr)
        data = resample(data, n_samples).astype(np.float32)
    return data


def simulate_fire_and_forget(transcriber, audio: np.ndarray,
                              silence_threshold: float = 1.0,
                              min_buffer: float = 0.5) -> dict:
    """Simulate fire-and-forget: buffer until silence, transcribe chunk, clear.

    Simulates the legacy pipeline by chunking audio at silence boundaries
    and transcribing each chunk independently.
    """
    chunk_size = int(SAMPLE_RATE * 0.064)  # 64ms frames like real app
    silence_rms_threshold = 0.003
    silence_frames_needed = int(silence_threshold / 0.064)

    results = []
    latencies = []
    call_count = 0
    total_transcribe_time = 0.0

    buf = AudioBuffer()
    silence_count = 0
    had_speech = False

    start_wall = time.monotonic()

    for i in range(0, len(audio), chunk_size):
        chunk = audio[i:i + chunk_size]
        if len(chunk) < chunk_size:
            chunk = np.pad(chunk, (0, chunk_size - len(chunk)))

        rms = float(np.sqrt(np.mean(chunk ** 2)))
        buf.append(chunk)

        if rms > silence_rms_threshold:
            had_speech = True
            silence_count = 0
        else:
            silence_count += 1

        # Fire on silence after speech
        if had_speech and silence_count >= silence_frames_needed and buf.duration >= min_buffer:
            audio_chunk = buf.get_and_clear()
            t0 = time.monotonic()
            result = transcriber.transcribe(audio_chunk)
            t1 = time.monotonic()
            latency = t1 - t0
            latencies.append(latency)
            total_transcribe_time += latency
            call_count += 1

            text = result.get("text", "").strip()
            if text:
                results.append({
                    "text": text,
                    "latency_ms": round(latency * 1000, 1),
                    "audio_sec": round(len(audio_chunk) / SAMPLE_RATE, 2),
                })

            had_speech = False
            silence_count = 0

    # Flush remaining
    if buf.duration > min_buffer:
        audio_chunk = buf.get_and_clear()
        t0 = time.monotonic()
        result = transcriber.transcribe(audio_chunk)
        t1 = time.monotonic()
        latency = t1 - t0
        latencies.append(latency)
        total_transcribe_time += latency
        call_count += 1
        text = result.get("text", "").strip()
        if text:
            results.append({
                "text": text,
                "latency_ms": round(latency * 1000, 1),
                "audio_sec": round(len(audio_chunk) / SAMPLE_RATE, 2),
            })

    wall_time = time.monotonic() - start_wall

    all_text = " ".join(r["text"] for r in results)
    return {
        "method": "Fire-and-Forget",
        "results": results,
        "full_text": all_text,
        "word_count": len(all_text.split()) if all_text.strip() else 0,
        "call_count": call_count,
        "total_transcribe_time_ms": round(total_transcribe_time * 1000, 1),
        "wall_time_ms": round(wall_time * 1000, 1),
        "latencies_ms": [round(l * 1000, 1) for l in latencies],
        "mean_latency_ms": round(np.mean(latencies) * 1000, 1) if latencies else 0,
        "p50_latency_ms": round(np.percentile(latencies, 50) * 1000, 1) if latencies else 0,
        "p95_latency_ms": round(np.percentile(latencies, 95) * 1000, 1) if latencies else 0,
        "max_latency_ms": round(max(latencies) * 1000, 1) if latencies else 0,
    }


def simulate_agreement(transcriber, audio: np.ndarray,
                        tick_interval: float = AGREEMENT_TICK_SECONDS,
                        min_audio: float = AGREEMENT_MIN_AUDIO,
                        flush_silence: float = AGREEMENT_FLUSH_SILENCE) -> dict:
    """Simulate LocalAgreement: overlapping ticks, commit on consensus.

    Simulates the new pipeline by transcribing every tick_interval seconds
    and committing only words that two consecutive ticks agree on.
    """
    chunk_size = int(SAMPLE_RATE * 0.064)  # 64ms frames
    silence_rms_threshold = 0.003
    silence_frames_needed = int(flush_silence / 0.064)

    committed_results = []
    latencies = []
    call_count = 0
    total_transcribe_time = 0.0
    ticks_with_no_commit = 0
    ticks_with_commit = 0

    agreement = AgreementState()
    buf = AudioBuffer()
    silence_count = 0
    had_speech = False
    last_tick_sample = 0
    tick_samples = int(tick_interval * SAMPLE_RATE)

    start_wall = time.monotonic()

    for i in range(0, len(audio), chunk_size):
        chunk = audio[i:i + chunk_size]
        if len(chunk) < chunk_size:
            chunk = np.pad(chunk, (0, chunk_size - len(chunk)))

        rms = float(np.sqrt(np.mean(chunk ** 2)))
        buf.append(chunk)
        current_sample = i + chunk_size

        if rms > silence_rms_threshold:
            had_speech = True
            silence_count = 0
        else:
            silence_count += 1

        # Tick: periodic transcription while speech active
        if had_speech and buf.duration >= min_audio and (current_sample - last_tick_sample) >= tick_samples:
            audio_window = buf.get_audio()
            t0 = time.monotonic()
            result = transcriber.transcribe(audio_window)
            t1 = time.monotonic()
            latency = t1 - t0
            latencies.append(latency)
            total_transcribe_time += latency
            call_count += 1
            last_tick_sample = current_sample

            text = result.get("text", "").strip()
            newly_committed, pending = agreement.tick(text)

            # Trim buffer on commit
            if newly_committed:
                audio_duration = len(audio_window) / SAMPLE_RATE
                trim_secs = agreement.get_trim_seconds(audio_duration)
                if trim_secs > 0:
                    buf.trim_front(trim_secs)
                ticks_with_commit += 1
                committed_results.append({
                    "text": newly_committed.strip(),
                    "pending": pending[:50],
                    "latency_ms": round(latency * 1000, 1),
                    "audio_sec": round(len(audio_window) / SAMPLE_RATE, 2),
                })
            else:
                ticks_with_no_commit += 1

        # Flush on silence
        if had_speech and silence_count >= silence_frames_needed:
            flush_text = agreement.flush_all()
            if flush_text.strip():
                committed_results.append({
                    "text": flush_text.strip(),
                    "pending": "",
                    "latency_ms": 0,
                    "audio_sec": 0,
                    "flushed": True,
                })
            buf.clear()
            had_speech = False
            silence_count = 0
            agreement.reset()

    # Final flush
    flush_text = agreement.flush_all()
    if flush_text.strip():
        committed_results.append({
            "text": flush_text.strip(),
            "pending": "",
            "latency_ms": 0,
            "audio_sec": 0,
            "flushed": True,
        })

    wall_time = time.monotonic() - start_wall

    all_text = " ".join(r["text"] for r in committed_results)
    return {
        "method": "LocalAgreement",
        "results": committed_results,
        "full_text": all_text,
        "word_count": len(all_text.split()) if all_text.strip() else 0,
        "call_count": call_count,
        "ticks_with_commit": ticks_with_commit,
        "ticks_with_no_commit": ticks_with_no_commit,
        "total_transcribe_time_ms": round(total_transcribe_time * 1000, 1),
        "wall_time_ms": round(wall_time * 1000, 1),
        "latencies_ms": [round(l * 1000, 1) for l in latencies],
        "mean_latency_ms": round(np.mean(latencies) * 1000, 1) if latencies else 0,
        "p50_latency_ms": round(np.percentile(latencies, 50) * 1000, 1) if latencies else 0,
        "p95_latency_ms": round(np.percentile(latencies, 95) * 1000, 1) if latencies else 0,
        "max_latency_ms": round(max(latencies) * 1000, 1) if latencies else 0,
    }


def word_overlap(text_a: str, text_b: str) -> dict:
    """Compute word overlap metrics between two texts."""
    words_a = set(text_a.lower().split())
    words_b = set(text_b.lower().split())
    if not words_a or not words_b:
        return {"jaccard": 0.0, "overlap_a": 0.0, "overlap_b": 0.0}
    intersection = words_a & words_b
    union = words_a | words_b
    return {
        "jaccard": round(len(intersection) / len(union), 3),
        "common_words": len(intersection),
        "unique_to_a": len(words_a - words_b),
        "unique_to_b": len(words_b - words_a),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3-0.6b",
                        help="Model key (e.g., qwen3-0.6b, qwen3-1.7b)")
    parser.add_argument("--audio", default="tests/fixtures/speakers/dev00.wav",
                        help="Path to WAV file")
    parser.add_argument("--output", default="tests/benchmark_results.json",
                        help="Output JSON path")
    args = parser.parse_args()

    from transcriber.engine import Qwen3Transcriber, QWEN3_MODELS

    model_id = QWEN3_MODELS.get(args.model, args.model)
    print(f"Loading model: {args.model} ({model_id})...")
    transcriber = Qwen3Transcriber(model=model_id, language="en")
    transcriber.load()
    print("Model loaded.\n")

    print(f"Loading audio: {args.audio}")
    audio = load_audio(args.audio)
    duration = len(audio) / SAMPLE_RATE
    print(f"Audio duration: {duration:.1f}s ({len(audio)} samples)\n")

    # Warm up the model with a short transcription
    print("Warming up model...")
    warmup = audio[:int(SAMPLE_RATE * 2)]
    transcriber._recent.clear()  # reset dedup
    transcriber.transcribe(warmup)
    transcriber._recent.clear()
    print("Warmup done.\n")

    # Run fire-and-forget
    print("=" * 60)
    print("Running: Fire-and-Forget")
    print("=" * 60)
    transcriber._recent.clear()
    ff_results = simulate_fire_and_forget(transcriber, audio)
    print(f"  Calls: {ff_results['call_count']}")
    print(f"  Words: {ff_results['word_count']}")
    print(f"  Total transcribe time: {ff_results['total_transcribe_time_ms']:.0f}ms")
    print(f"  Wall time: {ff_results['wall_time_ms']:.0f}ms")
    print(f"  Mean latency: {ff_results['mean_latency_ms']:.0f}ms")
    print(f"  P95 latency:  {ff_results['p95_latency_ms']:.0f}ms")
    print()

    # Run LocalAgreement
    print("=" * 60)
    print("Running: LocalAgreement (overlapping chunks)")
    print("=" * 60)
    transcriber._recent.clear()
    la_results = simulate_agreement(transcriber, audio)
    print(f"  Calls: {la_results['call_count']}")
    print(f"  Commits: {la_results['ticks_with_commit']}, No-commit ticks: {la_results['ticks_with_no_commit']}")
    print(f"  Words: {la_results['word_count']}")
    print(f"  Total transcribe time: {la_results['total_transcribe_time_ms']:.0f}ms")
    print(f"  Wall time: {la_results['wall_time_ms']:.0f}ms")
    print(f"  Mean latency: {la_results['mean_latency_ms']:.0f}ms")
    print(f"  P95 latency:  {la_results['p95_latency_ms']:.0f}ms")
    print()

    # Compare
    overlap = word_overlap(ff_results["full_text"], la_results["full_text"])
    print("=" * 60)
    print("Comparison")
    print("=" * 60)
    print(f"  Word overlap (Jaccard): {overlap['jaccard']:.1%}")
    print(f"  Common words: {overlap['common_words']}")
    print(f"  Unique to F&F: {overlap['unique_to_a']}")
    print(f"  Unique to LA:  {overlap['unique_to_b']}")
    print()

    # Print transcripts side by side
    print("--- Fire-and-Forget transcript ---")
    for r in ff_results["results"]:
        print(f"  [{r['latency_ms']:>6.0f}ms | {r['audio_sec']:.1f}s] {r['text'][:80]}")
    print()
    print("--- LocalAgreement transcript ---")
    for r in la_results["results"]:
        flushed = " [FLUSH]" if r.get("flushed") else ""
        print(f"  [{r['latency_ms']:>6.0f}ms | {r['audio_sec']:.1f}s] {r['text'][:80]}{flushed}")
    print()

    # Save results
    output = {
        "audio_file": args.audio,
        "audio_duration_sec": round(duration, 1),
        "model": args.model,
        "fire_and_forget": ff_results,
        "local_agreement": la_results,
        "comparison": overlap,
        "config": {
            "tick_interval": AGREEMENT_TICK_SECONDS,
            "min_audio": AGREEMENT_MIN_AUDIO,
            "flush_silence": AGREEMENT_FLUSH_SILENCE,
        },
    }

    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Results saved to {args.output}")


if __name__ == "__main__":
    main()
