plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

android {
    namespace = "org.openledger.android"
    compileSdk = 36
    buildToolsVersion = "35.0.0"

    defaultConfig {
        applicationId = "org.openledger.android"
        minSdk = 24
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0-alpha1"
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildTypes {
        debug {
            applicationIdSuffix = ".preview"
            versionNameSuffix = "-preview"
        }
        release {
            isMinifyEnabled = false
        }
    }
    lint {
        abortOnError = true
        checkReleaseBuilds = true
    }
}

kotlin {
    compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) }
}

// Package an explicit dependency closure from the shared source, never a copied
// second implementation or the Windows UI, providers, or personal settings.
val sharedPython = layout.buildDirectory.dir("generated/shared-python")
val stageSharedPython by
    tasks.registering(Sync::class) {
        from("../../src") {
            include("openledger/__init__.py", "openledger/_version.py")
            include("openledger/domain/**/*.py")
            include("openledger/application/__init__.py", "openledger/application/parsing.py")
            include(
                "openledger/application/dto/__init__.py",
                "openledger/application/dto/ledger.py",
            )
            include(
                "openledger/application/dto/parsing.py",
                "openledger/application/dto/queries.py",
            )
            include("openledger/application/dto/results.py")
            include("openledger/infrastructure/__init__.py", "openledger/infrastructure/runtime.py")
            include("openledger/infrastructure/ledger.py", "openledger/infrastructure/queries.py")
            include("openledger/infrastructure/integrity.py")
            include("openledger/infrastructure/database/**/*.py")
            include("openledger/mobile/**/*.py")
            include("openledger/resources/__init__.py", "openledger/resources/migrations/*.sql")
        }
        into(sharedPython)
    }

chaquopy {
    defaultConfig {
        version = "3.12"
        buildPython(providers.environmentVariable("OPENLEDGER_BUILD_PYTHON").orElse("python").get())
        pip { install("tzdata==2026.4") }
    }
    sourceSets { getByName("main") { srcDir(sharedPython) } }
}

tasks.configureEach {
    if (name != "stageSharedPython" && name.contains("Python")) {
        dependsOn(stageSharedPython)
    }
}

dependencyLocking { lockAllConfigurations() }

dependencies {
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test:core:1.6.1")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.uiautomator:uiautomator:2.3.0")
}
