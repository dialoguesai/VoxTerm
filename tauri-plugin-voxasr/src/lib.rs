//! VoxTerm on-device ASR plugin (Android). The phone records + transcribes locally via the
//! sherpa-onnx Android AAR — no pairing, no relay, no network. The webview drives it through
//! two commands (`start_transcribe`/`stop_transcribe`) and listens for `voxasr://partial` and
//! `voxasr://final` events the native plugin emits. Desktop/iOS get an honest "unsupported"
//! error (desktop uses the full Python engine; iOS is a future native port).

use tauri::{
    plugin::{Builder, TauriPlugin},
    AppHandle, Manager, Runtime,
};

/// Holds the Android plugin handle (the bridge to the Kotlin `VoxasrPlugin`). On non-Android
/// targets it just carries the app handle so the commands can return a clean error.
struct Voxasr<R: Runtime> {
    #[cfg(target_os = "android")]
    handle: tauri::plugin::PluginHandle<R>,
    #[cfg(not(target_os = "android"))]
    _app: AppHandle<R>,
}

#[tauri::command]
async fn start_transcribe<R: Runtime>(app: AppHandle<R>) -> Result<(), String> {
    #[cfg(target_os = "android")]
    {
        // The native plugin uses the bundled, staged model — no args needed.
        app.state::<Voxasr<R>>()
            .handle
            .run_mobile_plugin::<()>("startTranscribe", ())
            .map_err(|e| e.to_string())
    }
    #[cfg(not(target_os = "android"))]
    {
        let _ = app;
        Err("on-device transcription is Android-only; use the desktop engine elsewhere".into())
    }
}

#[tauri::command]
async fn stop_transcribe<R: Runtime>(app: AppHandle<R>) -> Result<(), String> {
    #[cfg(target_os = "android")]
    {
        app.state::<Voxasr<R>>()
            .handle
            .run_mobile_plugin::<()>("stopTranscribe", ())
            .map_err(|e| e.to_string())
    }
    #[cfg(not(target_os = "android"))]
    {
        let _ = app;
        Err("on-device transcription is Android-only".into())
    }
}

pub fn init<R: Runtime>() -> TauriPlugin<R> {
    Builder::new("voxasr")
        .invoke_handler(tauri::generate_handler![start_transcribe, stop_transcribe])
        .setup(|app, _api| {
            #[cfg(target_os = "android")]
            {
                let handle = _api.register_android_plugin("site.nubs.voxterm.voxasr", "VoxasrPlugin")?;
                app.manage(Voxasr { handle });
            }
            #[cfg(not(target_os = "android"))]
            {
                app.manage(Voxasr { _app: app.clone() });
            }
            Ok(())
        })
        .build()
}
