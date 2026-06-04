package site.nubs.voxterm.voxasr

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
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
    private var recognizer: OnlineRecognizer? = null

    // The streaming model is bundled in assets/voxterm-model and staged to filesDir on first
    // use, so transcription is fully offline (no first-run download).
    private fun stagedModelDir(): File {
        val out = File(activity.filesDir, "voxterm-model")
        if (!File(out, "tokens.txt").exists()) {
            out.mkdirs()
            val am = activity.assets
            for (name in am.list("voxterm-model") ?: arrayOf()) {
                am.open("voxterm-model/$name").use { input ->
                    File(out, name).outputStream().use { input.copyTo(it) }
                }
            }
        }
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
            invoke.resolve()
            return
        }
        running = true
        worker = thread(start = true) {
            var audio: AudioRecord? = null
            try {
                val rec = recognizer ?: buildRecognizer(stagedModelDir()).also { recognizer = it }
                val stream = rec.createStream()
                val sampleRate = 16000
                val minBuf = AudioRecord.getMinBufferSize(
                    sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
                )
                audio = AudioRecord(
                    MediaRecorder.AudioSource.MIC, sampleRate,
                    AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, minBuf * 2
                )
                val buf = ShortArray(minBuf)
                audio.startRecording()
                while (running) {
                    val n = audio.read(buf, 0, buf.size)
                    if (n <= 0) continue
                    val samples = FloatArray(n) { buf[it] / 32768.0f }
                    stream.acceptWaveform(samples, sampleRate)
                    while (rec.isReady(stream)) rec.decode(stream)
                    val text = rec.getResult(stream).text
                    if (text.isNotEmpty()) trigger("partial", JSObject().put("text", text))
                    if (rec.isEndpoint(stream)) {
                        if (text.isNotEmpty()) trigger("final", JSObject().put("text", text))
                        rec.reset(stream)
                    }
                }
                stream.release()
            } catch (e: Exception) {
                trigger("error", JSObject().put("message", e.message ?: "transcription error"))
            } finally {
                try { audio?.stop() } catch (_: Exception) {}
                audio?.release()
            }
        }
        invoke.resolve()
    }

    @Command
    fun stopTranscribe(invoke: Invoke) {
        running = false
        worker?.join(2000)
        worker = null
        invoke.resolve()
    }
}
