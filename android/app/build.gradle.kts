plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android { namespace = "cc.duyuan.hkvpn"; compileSdk = 35
    defaultConfig { applicationId = "cc.duyuan.hkvpn"; minSdk = 23; targetSdk = 35; versionCode = 2; versionName = "1.1.0" }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildTypes {
        getByName("release") {
            // Personal-use default: emit an installable APK. Replace this with a
            // private release keystore before distributing it to other people.
            signingConfig = signingConfigs.getByName("debug")
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.activity:activity-ktx:1.10.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("com.wireguard.android:tunnel:1.0.20230706")
}
