plugins {
    id("com.android.application")
}

android {
    namespace = "com.ugvuav.gcs"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.ugvuav.gcs"
        minSdk = 26
        targetSdk = 34
        versionCode = 5
        versionName = "1.2"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            // Signed with the debug key so a release APK installs without extra setup.
            // Replace with your own key before distributing the app.
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }
}
