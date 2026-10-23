plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "app.northernlights.tv"
    compileSdk = 35

    defaultConfig {
        applicationId = "app.northernlights.tv"
        minSdk = 24
        targetSdk = 35
        // GitHub's run number, so each CI build is a newer version and Settings > Apps shows which one is installed.
        val build = System.getenv("GITHUB_RUN_NUMBER")?.toIntOrNull() ?: 1
        versionCode = build
        versionName = "1.0.$build"
    }

    // A fixed debug key, committed on purpose: CI otherwise signs each build with a fresh random
    // key and Android refuses to install an update over the copy on the TV. It is a debug key
    // with the standard public password, not a secret; a real release key must never go here.
    signingConfigs {
        getByName("debug") {
            storeFile = file("debug.keystore")
            storePassword = "android"
            keyAlias = "androiddebugkey"
            keyPassword = "android"
        }
    }

    buildTypes {
        debug {
            signingConfig = signingConfigs.getByName("debug")
        }
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    lint {
        abortOnError = false
        warningsAsErrors = false
        checkReleaseBuilds = false
    }
}

dependencies {
    val media3 = "1.5.1"
    implementation("androidx.media3:media3-exoplayer:$media3")
    implementation("androidx.media3:media3-exoplayer-hls:$media3")
    implementation("androidx.media3:media3-database:$media3")
    implementation("androidx.media3:media3-ui:$media3")
    implementation("androidx.media3:media3-session:$media3")
    // Jellyfin's prebuilt FFmpeg audio decoder (TrueHD, DTS, ...). 1.5.0+1 is built for Media3 1.5.0 and is the newest
    // release for the 1.5.x line; the module's classes are unchanged in 1.5.1. Maven Central.
    implementation("org.jellyfin.media3:media3-ffmpeg-decoder:1.5.0+1")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    testImplementation("junit:junit:4.13.2")
}
