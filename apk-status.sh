#!/usr/bin/env bash
# Says whether the built APK is behind the Android sources.
cd "$(dirname "$0")" || exit 1
APK="android/app/build/outputs/apk/debug/app-debug.apk"

if [ ! -f "$APK" ]; then
    echo "No APK built yet.  Run:  ./build-apk.ps1"
    exit 1
fi

CHANGED=$(find android/app/src -type f \( -name '*.java' -o -name '*.xml' -o -name '*.png' \) \
          -newer "$APK" 2>/dev/null)

if [ -z "$CHANGED" ]; then
    echo "APK is up to date -- nothing to reinstall."
    echo "  built: $(date -r "$APK" '+%Y-%m-%d %H:%M')"
else
    echo "The APK is OUT OF DATE. Rebuild and reinstall it:"
    echo "  ./build-apk.ps1"
    echo
    echo "changed since the last build:"
    echo "$CHANGED" | sed 's|^|    |'
fi
