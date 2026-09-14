import java.util.Properties

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
    id("com.google.gms.google-services")
}

// Public setup instructions: ../RELEASE_SIGNING.md. Never log these values.
val localSigning = Properties().apply {
    val configFile = rootProject.file("key.properties")
    if (configFile.isFile) configFile.inputStream().use { load(it) }
}
val releaseSigning = mapOf(
    "storeFile" to "ECOMSBD_RELEASE_STORE_FILE",
    "storePassword" to "ECOMSBD_RELEASE_STORE_PASSWORD",
    "keyAlias" to "ECOMSBD_RELEASE_KEY_ALIAS",
    "keyPassword" to "ECOMSBD_RELEASE_KEY_PASSWORD",
).mapValues { (property, environment) ->
    providers.environmentVariable(environment).orNull?.takeIf { it.isNotBlank() }
        ?: localSigning.getProperty(property)?.takeIf { it.isNotBlank() }
}
val releaseStoreFile = releaseSigning["storeFile"]?.let { rootProject.file(it) }

// Run only for release tasks: a developer without signing secrets can still
// sync Gradle and build/debug normally. Never fall back to an unsigned/debug release.
val validateReleaseSigning = tasks.register("validateReleaseSigning") {
    group = "verification"
    description = "Check private release signing configuration without building the app."
    doLast {
        val missing = releaseSigning.filterValues { it == null }.keys
        check(missing.isEmpty()) {
            "Release signing is required. Missing: ${missing.joinToString()}. " +
                "Configure android/key.properties or ECOMSBD_RELEASE_*; see RELEASE_SIGNING.md."
        }
        check(releaseStoreFile?.isFile == true) { "Release keystore file does not exist." }
        check(!releaseSigning["keyAlias"].equals("androiddebugkey", ignoreCase = true) &&
            !releaseStoreFile!!.name.equals("debug.keystore", ignoreCase = true)) {
            "The Android debug key must not be used for production releases."
        }
    }
}
tasks.matching { it.name == "preReleaseBuild" || it.name == "validateSigningRelease" }
    .configureEach { dependsOn(validateReleaseSigning) }

android {
    // Permanent Android identity, explicitly chosen by the owner.
    namespace = "com.smply.app"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = "com.smply.app"

        // Android-first, low-end devices in scope (master spec section 53).
        // minSdk comes from the Flutter toolchain so plugin requirements and the
        // declared floor cannot drift apart.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        create("release") {
            storeFile = releaseStoreFile
            storePassword = releaseSigning["storePassword"]
            keyAlias = releaseSigning["keyAlias"]
            keyPassword = releaseSigning["keyPassword"]
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.getByName("release")

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
