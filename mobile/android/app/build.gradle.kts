plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.atlascontrol.mobile"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.atlascontrol.mobile"
        minSdk = 29
        targetSdk = 36
        versionCode = 2
        versionName = "2.0.0"
    }

    buildFeatures {
        compose = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2026.02.01")

    implementation(composeBom)
    androidTestImplementation(composeBom)

    // Core
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.10.0")

    // Compose
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.compose.foundation:foundation")

    // HTTP (TLS pinned to each Atlas's own key — see network/AtlasTrust.kt)
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // QR code on the Atlas label: CameraX preview + on-device ZXing decoding
    // (no Google Play services, works offline).
    implementation("androidx.camera:camera-camera2:1.4.2")
    implementation("androidx.camera:camera-lifecycle:1.4.2")
    implementation("androidx.camera:camera-view:1.4.2")
    implementation("com.google.zxing:core:3.5.3")

    // Coroutines
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.10.2")

    // Okio (transitive from OkHttp but declared explicitly for direct Buffer usage)
    implementation("com.squareup.okio:okio:3.9.0")

    debugImplementation("androidx.compose.ui:ui-tooling")
}
