# Installing VoxTerm on Android

VoxTerm ships an on-device Android app: it records the mic and transcribes
**fully offline** with Whisper — no account, no network, no Python. The APK even
ships **without the INTERNET permission** (microphone only).

- **Works on:** arm64-v8a phones, **Android 7.0+ (API 24)**. (Virtually every
  phone from ~2017 on is arm64.)
- **Size:** ~150–230 MB — it bundles the Whisper speech model so it works offline.
- **Scope:** record → stop → transcript, plus export. Diarization, AI-summarize,
  and system-audio capture are desktop-only and hidden on the phone.

---

## Option A — one-off install (sideload the APK)

1. On your phone, open the **[latest Android build](https://github.com/dmarzzz/VoxTerm/releases/tag/android-latest)**
   (or any tagged release) and download **`VoxTerm-android-arm64.apk`**.
2. Tap the downloaded file. Android will warn that this source isn't allowed to
   install apps — tap **Settings**, enable **“Allow from this source”** (this is
   per-app, e.g. for your browser or Files app), then go back.
3. Tap **Install**, then **Open**.
4. On first record, grant the **microphone** permission.

That's it — record, talk, stop, read your transcript. You can turn Wi-Fi/data off
entirely; transcription runs on the phone.

> Quiet/blocked install? Make sure you grabbed the `.apk` (not the source zip),
> and that your phone is arm64 + Android 7+.

---

## Option B — auto-updating (recommended): Obtainium

[Obtainium](https://github.com/ImranR98/Obtainium) installs apps straight from
their release page and notifies/updates you when a new build ships.

1. Install Obtainium (from its GitHub releases or F-Droid).
2. **Add App** → paste:
   `https://github.com/dmarzzz/VoxTerm`
3. If prompted, point it at the **`android-latest`** release (or enable
   pre-releases) and let it pick `VoxTerm-android-arm64.apk`.
4. Tap **Add**, then **Install**.

Every new CI build bumps the app's internal version, so Obtainium will offer the
update automatically. (Obtainium installs the developer's own signed build, and
all future updates must match that signature — see below.)

---

## When does a new APK get published?

CI (`.github/workflows/android-release.yml`) rebuilds and republishes the APK when:

- a **mobile path changes on `main`** (the native plugin `tauri-plugin-voxasr/`,
  the Tauri shell `src-tauri/`, the shared GUI in `gui/static/`, or the
  `mobile-pair/` bundle) → updates the rolling **`android-latest`** release;
- you trigger it **manually** from the Actions tab (with an optional model size).

> Heads-up: the phone bundles a **separate** native Whisper engine, not the
> desktop Python engine. UI/shell changes flow to the app on the next build;
> changes to the desktop Python ASR pipeline do **not** (the phone uses
> sherpa-onnx Whisper). See the project notes on engine divergence.

---

## Maintainers: signing

For stable, update-compatible releases, sign every build with the **same** key.
Generate a keystore once and store it in repo secrets.

```bash
# 1. Create a release keystore (keep release.jks SAFE and BACKED UP — losing it
#    means users must uninstall before they can install a future build).
keytool -genkeypair -v -keystore release.jks -alias voxterm \
  -keyalg RSA -keysize 2048 -validity 10000 \
  -storepass "<STORE_PASSWORD>" -keypass "<KEY_PASSWORD>" \
  -dname "CN=VoxTerm, O=VoxTerm, C=US"

# 2. Base64-encode it for the secret (macOS shown; on Linux use: base64 -w0 release.jks)
base64 -i release.jks | pbcopy
```

Then add four **repository secrets** (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | the base64 string from step 2 |
| `ANDROID_KEYSTORE_PASSWORD` | `<STORE_PASSWORD>` |
| `ANDROID_KEY_ALIAS` | `voxterm` |
| `ANDROID_KEY_PASSWORD` | `<KEY_PASSWORD>` |

With these set, CI signs every APK with your release key (the workflow reports
`Release-signed: true`). **Without** them, the job still builds an installable
APK using an ephemeral debug key (`Release-signed: false`) — handy for trying it
out, but updates across builds may require an uninstall because the signing key
changes each run.
