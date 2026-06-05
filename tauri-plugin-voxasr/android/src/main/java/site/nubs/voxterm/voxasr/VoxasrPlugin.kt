package site.nubs.voxterm.voxasr

import android.Manifest
import android.app.Activity
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import android.webkit.WebView
import androidx.core.content.ContextCompat
import app.tauri.annotation.Command
import app.tauri.annotation.TauriPlugin
import app.tauri.plugin.Invoke
import app.tauri.plugin.JSObject
import app.tauri.plugin.Plugin
import com.k2fsa.sherpa.onnx.EndpointConfig
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.OnlineModelConfig
import com.k2fsa.sherpa.onnx.OnlineRecognizer
import com.k2fsa.sherpa.onnx.OnlineRecognizerConfig
import com.k2fsa.sherpa.onnx.OnlineStream
import com.k2fsa.sherpa.onnx.OnlineTransducerModelConfig
import java.io.File
import kotlin.concurrent.thread

/**
 * On-device streaming ASR. The mic is read with AudioRecord (16 kHz mono PCM16), fed to a
 * sherpa-onnx OnlineRecognizer, and the running decode is emitted as `partial`; sherpa's
 * endpoint detection finalizes a line as `final`. Everything is local — no network.
 */
@TauriPlugin
class VoxasrPlugin(private val activity: Activity) : Plugin(activity) {
    @Volatile private var running = false
    private var worker: Thread? = null
    // @Volatile: built lazily from both the mic worker and the debug self-test thread.
    @Volatile private var recognizer: OnlineRecognizer? = null
    private val modelFiles = listOf("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")
    // Transcript state the webview polls (avoids the plugin-event listener path).
    private val finalLines = java.util.Collections.synchronizedList(mutableListOf<String>())
    @Volatile private var partial = ""
    @Volatile private var lastError: String? = null

    // The streaming model is bundled in assets/voxterm-model and staged to filesDir on first
    // use, so transcription is fully offline (no first-run download).
    private fun stagedModelDir(): File {
        val out = File(activity.filesDir, "voxterm-model")
        // Sentinel = ALL required files present (not just tokens.txt) so a half-copied dir
        // from a mid-copy process kill self-heals instead of wedging.
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

    private fun buildRecognizer(dir: File): OnlineRecognizer {
        val config = OnlineRecognizerConfig(
            featConfig = FeatureConfig(sampleRate = 16000, featureDim = 80),
            modelConfig = OnlineModelConfig(
                transducer = OnlineTransducerModelConfig(
                    encoder = File(dir, "encoder.int8.onnx").absolutePath,
                    decoder = File(dir, "decoder.int8.onnx").absolutePath,
                    joiner = File(dir, "joiner.int8.onnx").absolutePath,
                ),
                tokens = File(dir, "tokens.txt").absolutePath,
                numThreads = 2,
                modelType = "zipformer",
            ),
            endpointConfig = EndpointConfig(),
            enableEndpoint = true,
        )
        return OnlineRecognizer(config = config)
    }

    @Command
    fun startTranscribe(invoke: Invoke) {
        if (ContextCompat.checkSelfPermission(activity, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            invoke.reject("microphone permission not granted")
            return
        }
        if (running) {
            invoke.resolve(JSObject())
            return
        }
        running = true
        synchronized(finalLines) { finalLines.clear() }
        partial = ""
        lastError = null
        worker = thread(start = true) {
            var audio: AudioRecord? = null
            var stream: OnlineStream? = null
            try {
                val rec = recognizer ?: buildRecognizer(stagedModelDir()).also { recognizer = it }
                val s = rec.createStream()
                stream = s                                  // tracked for release in finally
                val sampleRate = 16000
                val minBuf = AudioRecord.getMinBufferSize(
                    sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
                )
                if (minBuf <= 0) {                          // ERROR/-1 or ERROR_BAD_VALUE/-2: minBuf*2 would throw
                    lastError = "audio buffer size unavailable ($minBuf)"
                    return@thread
                }
                audio = AudioRecord(
                    MediaRecorder.AudioSource.MIC, sampleRate,
                    AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, minBuf * 2
                )
                if (audio.state != AudioRecord.STATE_INITIALIZED) {   // mic busy / denied
                    lastError = "could not initialize the microphone"
                    return@thread
                }
                val buf = ShortArray(minBuf)
                audio.startRecording()
                while (running) {
                    val n = audio.read(buf, 0, buf.size)
                    if (n <= 0) continue
                    val samples = FloatArray(n) { buf[it] / 32768.0f }
                    s.acceptWaveform(samples, sampleRate)
                    while (rec.isReady(s)) rec.decode(s)
                    partial = rec.getResult(s).text
                    if (rec.isEndpoint(s)) {
                        val finalText = partial
                        if (finalText.isNotEmpty()) finalLines.add(finalText)
                        partial = ""
                        rec.reset(s)
                    }
                }
            } catch (e: Exception) {
                lastError = e.message ?: "transcription error"
            } finally {
                try { audio?.stop() } catch (_: Exception) {}
                audio?.release()
                try { stream?.release() } catch (_: Exception) {}   // never leak the native stream on a failed start
                running = false                                     // any exit path leaves a clean stopped state
            }
        }
        invoke.resolve(JSObject())
    }

    @Command
    fun stopTranscribe(invoke: Invoke) {
        running = false
        worker?.join(2000)
        worker = null
        invoke.resolve(JSObject())
    }

    // Return + clear the finalized lines since the last poll, plus the current (volatile) partial.
    @Command
    fun pollTranscript(invoke: Invoke) {
        val res = JSObject()
        res.put("partial", partial)
        val arr = org.json.JSONArray()
        synchronized(finalLines) {
            for (l in finalLines) arr.put(l)
            finalLines.clear()
        }
        res.put("finals", arr)
        lastError?.let { res.put("error", it); lastError = null }
        invoke.resolve(res)
    }

    // Debug self-test: on debuggable builds, decode the bundled test clip through the SAME
    // recognizer (file instead of mic) and log the result. Proves on-device decoding works
    // without a microphone (e.g. on an emulator). No-op on release builds.
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
            val rec = recognizer ?: buildRecognizer(dir).also { recognizer = it }
            val stream = rec.createStream()
            stream.acceptWaveform(readWav16kMono(test), 16000)
            stream.acceptWaveform(FloatArray(8000), 16000)   // trailing silence to flush the decode
            stream.inputFinished()
            while (rec.isReady(stream)) rec.decode(stream)
            val text = rec.getResult(stream).text
            stream.release()
            Log.i("voxasr", "SELFTEST_RESULT=[$text]")
        } catch (e: Exception) {
            Log.e("voxasr", "SELFTEST_ERROR", e)
        }
    }
}
