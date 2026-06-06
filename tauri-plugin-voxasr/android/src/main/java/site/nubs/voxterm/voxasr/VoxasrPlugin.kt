package site.nubs.voxterm.voxasr

import android.Manifest
import android.app.Activity
import android.content.pm.ApplicationInfo
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import android.webkit.WebView
import app.tauri.PermissionState
import app.tauri.annotation.Command
import app.tauri.annotation.Permission
import app.tauri.annotation.PermissionCallback
import app.tauri.annotation.TauriPlugin
import app.tauri.plugin.Invoke
import app.tauri.plugin.JSObject
import app.tauri.plugin.Plugin
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.OfflineModelConfig
import com.k2fsa.sherpa.onnx.OfflineRecognizer
import com.k2fsa.sherpa.onnx.OfflineRecognizerConfig
import com.k2fsa.sherpa.onnx.OfflineStream
import com.k2fsa.sherpa.onnx.OfflineWhisperModelConfig
import java.io.ByteArrayOutputStream
import java.io.File
import kotlin.concurrent.thread
import kotlin.math.sqrt

private const val SAMPLE_RATE = 16000
private const val WHISPER_WINDOW = 29 * SAMPLE_RATE   // just under Whisper's 30 s cap (it truncates >30 s)

/**
 * On-device speech-to-text. Records the mic with AudioRecord (16 kHz mono PCM16) into a buffer and,
 * at stop, transcribes the whole clip with an OFFLINE sherpa-onnx Whisper recognizer — full
 * context, native casing + punctuation, no live/streaming output. Everything is local — no network.
 *
 * The webview polls `pollTranscript`, which reports a phase (idle/recording/transcribing/done/error)
 * plus, when done, the transcript segments. Whisper decodes <=30 s per pass, so a long clip is split
 * into 30 s windows and the texts joined.
 */
@TauriPlugin(
    permissions = [Permission(strings = [Manifest.permission.RECORD_AUDIO], alias = "microphone")],
)
class VoxasrPlugin(private val activity: Activity) : Plugin(activity) {
    @Volatile private var running = false
    private var worker: Thread? = null
    // Bumped per capture session so a worker that outlives stop's join can't run the mic alongside,
    // or clobber the phase of, a newer session.
    @Volatile private var generation = 0
    // Built once via ensureRecognizer(); shared by the mic worker and the debug self-test.
    @Volatile private var recognizer: OfflineRecognizer? = null
    private val modelFiles = listOf("encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt")

    // Raw PCM16 of the current take. Written by the mic worker; the decode reads a snapshot captured
    // at stop (after the worker is joined), and a new take reassigns this field.
    @Volatile private var pcmBytes = ByteArrayOutputStream()
    // Polled state the webview reads. `phase` drives the GUI's record/transcribe/done state machine.
    @Volatile private var phase = "idle"          // idle | recording | transcribing | done | error
    @Volatile private var elapsedSec = 0.0
    @Volatile private var levelRms = 0.0
    @Volatile private var durationSec = 0.0
    @Volatile private var errorMsg: String? = null
    // Finalized transcript segments (one per <=30 s window): text + its start offset in seconds.
    private val segments = java.util.Collections.synchronizedList(mutableListOf<Pair<String, Double>>())

    // The Whisper model is bundled in assets/voxterm-model and staged to filesDir on first use, so
    // transcription is fully offline (no first-run download).
    private fun stagedModelDir(): File {
        val out = File(activity.filesDir, "voxterm-model")
        // Sentinel = ALL required files present so a half-copied dir self-heals instead of wedging.
        if (modelFiles.all { File(out, it).exists() }) return out
        // Copy into a temp dir, then atomically swap it in: `out` is only ever a complete dir.
        val tmp = File(activity.filesDir, "voxterm-model.tmp")
        tmp.deleteRecursively()
        tmp.mkdirs()
        val am = activity.assets
        for (name in am.list("voxterm-model") ?: arrayOf()) {
            am.open("voxterm-model/$name").use { input ->
                File(tmp, name).outputStream().use { input.copyTo(it) }
            }
        }
        out.deleteRecursively()
        tmp.renameTo(out)
        return out
    }

    private fun buildRecognizer(dir: File): OfflineRecognizer {
        val config = OfflineRecognizerConfig(
            featConfig = FeatureConfig(sampleRate = SAMPLE_RATE, featureDim = 80),
            modelConfig = OfflineModelConfig(
                whisper = OfflineWhisperModelConfig(
                    encoder = File(dir, "encoder.int8.onnx").absolutePath,
                    decoder = File(dir, "decoder.int8.onnx").absolutePath,
                    language = "en",          // bundled model is English-only (whisper *.en)
                    task = "transcribe",      // not "translate"
                ),
                tokens = File(dir, "tokens.txt").absolutePath,
                numThreads = 2,
                modelType = "whisper",
            ),
        )
        // assetManager defaults to null → the recognizer is built from the file paths above.
        return OfflineRecognizer(config = config)
    }

    // Build the recognizer once, lazily. @Synchronized so the mic worker and the debug self-test
    // thread can't both pass the null check and each build (then leak) a native recognizer.
    @Synchronized
    private fun ensureRecognizer(dir: File): OfflineRecognizer =
        recognizer ?: buildRecognizer(dir).also { recognizer = it }

    // Decode one <=30 s window in a single offline pass (no isReady/endpoint loop — that's online).
    private fun decodeChunk(rec: OfflineRecognizer, samples: FloatArray): String {
        val stream: OfflineStream = rec.createStream()
        try {
            stream.acceptWaveform(samples, SAMPLE_RATE)   // (samples, sampleRate)
            rec.decode(stream)
            return rec.getResult(stream).text.trim()
        } finally {
            stream.release()                              // never leak the native stream
        }
    }

    @Command
    fun startTranscribe(invoke: Invoke) {
        // The plugin owns the mic, so it owns acquiring the permission: on a fresh install the first
        // Start prompts (and resumes in micPermissionCallback) instead of hard-failing.
        if (getPermissionState("microphone") != PermissionState.GRANTED) {
            requestPermissionForAlias("microphone", invoke, "micPermissionCallback")
            return
        }
        beginCapture(invoke)
    }

    @PermissionCallback
    fun micPermissionCallback(invoke: Invoke) {
        if (getPermissionState("microphone") == PermissionState.GRANTED) {
            beginCapture(invoke)
        } else {
            invoke.reject("microphone permission is required for on-device transcription")
        }
    }

    private fun beginCapture(invoke: Invoke) {
        if (running) {
            invoke.resolve(JSObject())
            return
        }
        val gen = ++generation
        // Make sure a prior take's worker (e.g. one that outlived stop's 2 s join) has fully exited
        // and released the single-owner mic before we open a new AudioRecord — its loop guard
        // (generation == its own gen) is already false, so this join returns promptly.
        worker?.join(3000)
        running = true
        pcmBytes = ByteArrayOutputStream(SAMPLE_RATE * 2 * 60)   // ~1 min preallocated (32 KB/s)
        synchronized(segments) { segments.clear() }
        phase = "recording"; elapsedSec = 0.0; levelRms = 0.0; durationSec = 0.0; errorMsg = null
        worker = thread(start = true) {
            var audio: AudioRecord? = null
            try {
                val minBuf = AudioRecord.getMinBufferSize(
                    SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
                )
                if (minBuf <= 0) { fail("audio buffer size unavailable ($minBuf)", gen); return@thread }
                audio = AudioRecord(
                    MediaRecorder.AudioSource.MIC, SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, minBuf * 2
                )
                if (audio.state != AudioRecord.STATE_INITIALIZED) {
                    fail("could not initialize the microphone", gen); return@thread
                }
                val buf = ShortArray(minBuf)
                val bytes = ByteArray(minBuf * 2)
                val out = pcmBytes
                audio.startRecording()
                while (running && generation == gen) {
                    val n = audio.read(buf, 0, buf.size)
                    if (n <= 0) continue
                    var sumSq = 0.0
                    for (i in 0 until n) {
                        val s = buf[i].toInt()
                        bytes[2 * i] = (s and 0xff).toByte()
                        bytes[2 * i + 1] = ((s shr 8) and 0xff).toByte()
                        val v = s / 32768.0; sumSq += v * v
                    }
                    out.write(bytes, 0, n * 2)
                    levelRms = sqrt(sumSq / n)
                    elapsedSec = out.size() / 2.0 / SAMPLE_RATE
                }
            } catch (e: Exception) {
                fail(e.message ?: "recording error", gen)
            } finally {
                try { audio?.stop() } catch (_: Exception) {}
                audio?.release()
            }
        }
        invoke.resolve(JSObject())
    }

    private fun fail(msg: String, gen: Int) {
        if (generation == gen) { errorMsg = msg; phase = "error"; running = false }
    }

    @Command
    fun stopTranscribe(invoke: Invoke) {
        if (phase != "recording") { invoke.resolve(JSObject()); return }   // double-stop / never started
        running = false
        worker?.join(2000)                  // the worker exits on running=false; beginCapture re-joins
        invoke.resolve(JSObject())          // resolve now; transcription runs async, reported via poll
        val gen = generation
        val take = pcmBytes                 // capture THIS take's buffer (a new take reassigns the field)
        phase = "transcribing"
        thread(start = true) { decodeTake(take.toByteArray(), gen) }
    }

    // Transcribe a finished take: split into <=29 s windows (under Whisper's 30 s cap) and join.
    // Aborts quietly if a newer take started (generation changed) so it never clobbers its phase.
    private fun decodeTake(snapshot: ByteArray, gen: Int) {
        try {
            durationSec = snapshot.size / 2.0 / SAMPLE_RATE
            val total = snapshot.size / 2
            if (total == 0) { if (generation == gen) phase = "done"; return }
            val rec = ensureRecognizer(stagedModelDir())
            var off = 0
            while (off < total && generation == gen) {
                val end = windowEnd(snapshot, off, total)
                val samples = FloatArray(end - off) {
                    val b = (off + it) * 2
                    val s = ((snapshot[b + 1].toInt() shl 8) or (snapshot[b].toInt() and 0xff)).toShort()
                    s / 32768.0f
                }
                val text = decodeChunk(rec, samples)
                if (text.isNotEmpty()) segments.add(text to off / SAMPLE_RATE.toDouble())
                off = end
            }
            if (generation == gen) phase = "done"
        } catch (e: Exception) {
            if (generation == gen) { errorMsg = e.message ?: "transcription error"; phase = "error" }
        }
    }

    // End sample of the next decode window: the <=29 s cap, but if more audio remains, nudge the cut
    // to the quietest 10 ms frame in the last 2 s so a word straddling the boundary isn't sliced.
    private fun windowEnd(pcm: ByteArray, off: Int, total: Int): Int {
        val hardEnd = off + WHISPER_WINDOW
        if (hardEnd >= total) return total
        val frame = SAMPLE_RATE / 100                 // 10 ms
        var bestIdx = hardEnd
        var bestEnergy = Double.MAX_VALUE
        var i = off + WHISPER_WINDOW - 2 * SAMPLE_RATE
        while (i + frame <= hardEnd) {
            var sum = 0.0
            for (j in i until i + frame) {
                val b = j * 2
                val s = ((pcm[b + 1].toInt() shl 8) or (pcm[b].toInt() and 0xff)).toShort()
                val v = s / 32768.0; sum += v * v
            }
            if (sum < bestEnergy) { bestEnergy = sum; bestIdx = i + frame }
            i += frame
        }
        return bestIdx
    }

    // The webview polls this: { phase, elapsed, level, durationSec, error?, segments:[{text,start}] }.
    @Command
    fun pollTranscript(invoke: Invoke) {
        val res = JSObject()
        res.put("phase", phase)
        res.put("elapsed", elapsedSec)
        res.put("level", levelRms)
        res.put("durationSec", durationSec)
        errorMsg?.let { res.put("error", it) }
        val arr = org.json.JSONArray()
        synchronized(segments) {
            for ((text, start) in segments) {
                arr.put(org.json.JSONObject().put("text", text).put("start", start))
            }
        }
        res.put("segments", arr)
        invoke.resolve(res)
    }

    // Debug self-test: on debuggable builds, transcribe the bundled clip through the SAME offline
    // recognizer and log the result + xRT. Proves on-device decoding works without a mic. No-op on
    // release builds.
    override fun load(webView: WebView) {
        super.load(webView)
        val debuggable = (activity.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0
        if (debuggable) Thread { runSelfTest() }.start()
    }

    private fun readWav16kMono(f: File): FloatArray {
        val bytes = f.readBytes()                       // canonical 16k mono PCM16 WAV: 44-byte header
        val n = (bytes.size - 44) / 2
        val out = FloatArray(n)
        var j = 44
        for (i in 0 until n) {
            val s = ((bytes[j + 1].toInt() shl 8) or (bytes[j].toInt() and 0xff)).toShort()
            out[i] = s / 32768.0f
            j += 2
        }
        return out
    }

    private fun runSelfTest() {
        try {
            val dir = stagedModelDir()
            val test = File(dir, "test.wav")
            if (!test.exists()) { Log.i("voxasr", "SELFTEST_SKIP no test.wav"); return }
            val rec = ensureRecognizer(dir)
            val samples = readWav16kMono(test)
            val audioSec = samples.size / 16000.0
            val t0 = System.nanoTime()
            val text = decodeChunk(rec, samples)
            val decodeSec = (System.nanoTime() - t0) / 1e9
            // xRT < 1.0 = faster than real-time; logged so on-device latency is measured, not assumed.
            Log.i("voxasr", "SELFTEST_RESULT=[$text] xRT=%.2f (%.2fs decode / %.2fs audio)"
                .format(decodeSec / audioSec, decodeSec, audioSec))
        } catch (e: Exception) {
            Log.e("voxasr", "SELFTEST_ERROR", e)
        }
    }
}
