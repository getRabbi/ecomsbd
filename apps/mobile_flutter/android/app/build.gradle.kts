plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

android {
    // ==========================================================================
    // PACKAGE_ID_DECISION_REQUIRED
    // --------------------------------------------------------------------------
    // "com.example.ecomsbd" is the Flutter scaffold default and is a PLACEHOLDER.
    //
    // The operator must choose the final applicationId before the first Play
    // upload. Neither the master specification nor the UI prototype names one,
    // and it must not be guessed:
    //
    //   * Play rejects any id under com.example;
    //   * the applicationId is PERMANENT once published — it cannot be changed
    //     without shipping a different app and losing every install, review and
    //     subscription;
    //   * Google Play Billing purchase verification is bound to the package name
    //     (master spec section 90), so it must be settled before billing work.
    //
    // Changing it now costs one edit here plus the MainActivity package path and
    // the `namespace` below. Changing it after publication is not possible.
    //
    // Suggested shape once the operator picks a domain: com.<org>.ecomsbd
    // ==========================================================================
    namespace = "com.example.ecomsbd"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        // PACKAGE_ID_DECISION_REQUIRED — see the block above.
        applicationId = "com.example.ecomsbd"

        // Android-first, low-end devices in scope (master spec section 53).
        // minSdk comes from the Flutter toolchain so plugin requirements and the
        // declared floor cannot drift apart.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    buildTypes {
        release {
            // RELEASE_SIGNING_REQUIRED: still the debug keystore, so
            // `flutter run --release` works locally. A real upload key must be
            // provisioned before any Play track, and its credentials must live
            // outside this repository (android/key.properties, gitignored).
            signingConfig = signingConfigs.getByName("debug")

            // Shrinking is off until a release keystore and a crash-reporting
            // symbol upload exist; obfuscated stack traces with no mapping file
            // would make production crashes unreadable.
            isMinifyEnabled = false
            isShrinkResources = false
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}
