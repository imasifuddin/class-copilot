#!/usr/bin/env bash
# Builds the Android APK from Git Bash.
#   ./build-apk.sh              build it
#   ./build-apk.sh --install    build it and push to a USB-connected phone
#   ./build-apk.sh --clean      rebuild from scratch
set -e
cd "$(dirname "$0")"

# --- Android SDK ---
SDK="${ANDROID_HOME:-}"
[ -d "$SDK/platforms" ] || SDK="$LOCALAPPDATA/Android/Sdk"
if [ ! -d "$SDK/platforms" ]; then
    echo "Android SDK not found at $SDK" >&2
    echo "Get the command-line tools from https://developer.android.com/studio#command-tools" >&2
    echo "then run: sdkmanager \"platform-tools\" \"platforms;android-34\" \"build-tools;34.0.0\"" >&2
    exit 1
fi

# --- Gradle ---
GRADLE="$LOCALAPPDATA/gradle-dist/gradle-8.7/bin/gradle.bat"
if [ ! -f "$GRADLE" ]; then
    if command -v gradle >/dev/null 2>&1; then
        GRADLE="$(command -v gradle)"
    else
        echo "Gradle not found." >&2
        echo "Download the binary zip from https://gradle.org/releases/ and unzip it to" >&2
        echo "  $LOCALAPPDATA/gradle-dist/gradle-8.7" >&2
        exit 1
    fi
fi

# --- JDK 17+ ---
if [ -z "${JAVA_HOME:-}" ] && [ -d "/c/Program Files/Java/jdk-19" ]; then
    export JAVA_HOME="C:\Program Files\Java\jdk-19"
fi

export ANDROID_HOME="$SDK"
export ANDROID_SDK_ROOT="$SDK"
# gradle needs a Windows-style path here
printf 'sdk.dir=%s\n' "$(cygpath -w "$SDK" | sed 's/\/\\/g')" > android/local.properties

TASKS="assembleDebug"
INSTALL=0
for arg in "$@"; do
    case "$arg" in
        --clean)   TASKS="clean assembleDebug" ;;
        --install) INSTALL=1 ;;
    esac
done

( cd android && "$GRADLE" --no-daemon $TASKS )

APK="android/app/build/outputs/apk/debug/app-debug.apk"
if [ ! -f "$APK" ]; then
    echo "Gradle finished but no APK was produced." >&2
    exit 1
fi

SIZE=$(( $(stat -c %s "$APK") / 1024 ))
echo
echo "APK built: $(cygpath -w "$(pwd)/$APK")  (${SIZE} KB)"

if [ "$INSTALL" = "1" ]; then
    echo "Installing to the connected phone..."
    "$SDK/platform-tools/adb.exe" install -r "$APK"
else
    echo "Copy it to your phone and tap it to install."
    echo "Or, with the phone plugged in and USB debugging on:  ./build-apk.sh --install"
fi
