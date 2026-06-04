plugins {
    id("com.android.library")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "site.nubs.voxterm.voxasr"
    compileSdk = 36
    defaultConfig {
        minSdk = 24
    }
    buildTypes {
        getByName("release") {
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }
    kotlinOptions {
        jvmTarget = "1.8"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.9.0")
    implementation(project(":tauri-android"))
    // sherpa-onnx Android (statically-linked onnxruntime), version-matched to the desktop engine
    // (sherpa_onnx 1.13.2). Ships the JNI .so for arm64-v8a / armeabi-v7a / x86 / x86_64.
    implementation(files("libs/sherpa-onnx-1.13.2.aar"))
}
