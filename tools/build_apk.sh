#!/usr/bin/env bash
# Build the Android app (android/) into a signed APK without Android Studio or aapt2 - useful on
# ARM64 Linux (e.g. NVIDIA DGX Spark, Raspberry Pi 5), where aapt2 is not available.
# javac -> d8 -> manifest (studio_manifest.py + axml_encode.py) -> resources.arsc (launcher icon,
# arsc_encode.py) -> zip with assets -> apksigner.
#
# Needs: JDK 17+ (javac, java, javap, keytool), android.jar of platform 34, and from build-tools 35:
# lib/d8.jar, lib/apksigner.jar, core-lambda-stubs.jar. Point at them with environment variables:
#   JDK=/path/to/jdk/bin  ANDROID_JAR=/path/to/android.jar  BUILD_TOOLS=/path/to/build-tools/35.0.0 \
#   KEYSTORE=/path/to/your.keystore  tools/build_apk.sh
# Updates only install over an existing app when they are signed with the same keystore.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
M="$ROOT/android/app/src/main"
G="$ROOT/android/app/build.gradle.kts"
B="$ROOT/build/apk"
JDK="${JDK:-$(dirname "$(command -v javac)")}"
ANDROID_JAR="${ANDROID_JAR:-${ANDROID_HOME:-$HOME/Android/Sdk}/platforms/android-34/android.jar}"
BUILD_TOOLS="${BUILD_TOOLS:-${ANDROID_HOME:-$HOME/Android/Sdk}/build-tools/35.0.0}"
D8_JAR="${D8_JAR:-$BUILD_TOOLS/lib/d8.jar}"
APKSIGNER_JAR="${APKSIGNER_JAR:-$BUILD_TOOLS/lib/apksigner.jar}"
LAMBDA_STUBS="${LAMBDA_STUBS:-$BUILD_TOOLS/core-lambda-stubs.jar}"
KEYSTORE="${KEYSTORE:-$ROOT/build/debug.keystore}"
PY="${PYTHON:-python3}"
for f in "$JDK/javac" "$ANDROID_JAR" "$D8_JAR" "$APKSIGNER_JAR" "$LAMBDA_STUBS"; do
  [ -e "$f" ] || { echo "missing: $f (see the variables at the top of this script)"; exit 1; }
done

APP_ID=$(sed -n 's/.*applicationId = "\(.*\)".*/\1/p' "$G")
VCODE=$(sed -n 's/.*versionCode = \([0-9]*\).*/\1/p' "$G")
VNAME=$(sed -n 's/.*versionName = "\(.*\)".*/\1/p' "$G")
MIN=$(sed -n 's/.*minSdk = \([0-9]*\).*/\1/p' "$G")
TARGET=$(sed -n 's/.*targetSdk = \([0-9]*\).*/\1/p' "$G")
LABEL=$(sed -n 's/.*<string name="app_name">\(.*\)<\/string>.*/\1/p' "$M/res/values/strings.xml")
echo "Building $LABEL ($APP_ID) $VNAME ($VCODE), minSdk $MIN, targetSdk $TARGET"

rm -rf "$B"
mkdir -p "$B/classes" "$B/dex" "$B/apk/res/mipmap-xxxhdpi-v4"

echo "1/6 compiling Java"
"$JDK/javac" -source 8 -target 8 -Xlint:-options -nowarn -encoding UTF-8 \
  -bootclasspath "$ANDROID_JAR:$LAMBDA_STUBS" -d "$B/classes" $(find "$M/java" -name '*.java')

echo "2/6 converting to Android bytecode (d8)"
"$JDK/java" -cp "$D8_JAR" com.android.tools.r8.D8 --release --min-api "$MIN" \
  --lib "$ANDROID_JAR" --output "$B/dex" $(find "$B/classes" -name '*.class')

echo "3/6 manifest"
"$PY" -I "$ROOT/tools/studio_manifest.py" "$M/AndroidManifest.xml" "$B/AndroidManifest.flat.xml" \
  "$APP_ID" "$VCODE" "$VNAME" "$MIN" "$TARGET" "$LABEL"
"$PY" -I "$ROOT/tools/axml_encode.py" "$B/AndroidManifest.flat.xml" "$ANDROID_JAR" "$JDK/javap" "$B/apk/AndroidManifest.xml"

echo "4/6 resources (launcher icon)"
cp "$M/res/mipmap-xxxhdpi/ic_launcher.png" "$M/res/mipmap-xxxhdpi/ic_launcher_round.png" "$B/apk/res/mipmap-xxxhdpi-v4/"
( cd "$B/apk" && "$PY" -I "$ROOT/tools/arsc_encode.py" "$APP_ID" resources.arsc \
    res/mipmap-xxxhdpi-v4/ic_launcher.png res/mipmap-xxxhdpi-v4/ic_launcher_round.png )

echo "5/6 packaging"
"$PY" -I - "$B" "$M/assets" <<'EOF'
import os, sys, zipfile
b, assets = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(f"{b}/unsigned.apk", "w", zipfile.ZIP_DEFLATED) as z:
    # Android 11+ needs resources.arsc stored uncompressed on a 4-byte boundary: as the first
    # entry its data starts at 30 + len("resources.arsc") = 44.
    z.write(f"{b}/apk/resources.arsc", "resources.arsc", compress_type=zipfile.ZIP_STORED)
    z.write(f"{b}/apk/AndroidManifest.xml", "AndroidManifest.xml")
    z.write(f"{b}/dex/classes.dex", "classes.dex")
    for root, _, files in os.walk(f"{b}/apk/res"):
        for f in sorted(files):
            p = os.path.join(root, f)
            z.write(p, os.path.relpath(p, f"{b}/apk"), compress_type=zipfile.ZIP_STORED)
    for root, _, files in os.walk(assets):
        for f in sorted(files):
            p = os.path.join(root, f)
            z.write(p, "assets/" + os.path.relpath(p, assets))
with zipfile.ZipFile(f"{b}/unsigned.apk") as z:
    info = z.getinfo("resources.arsc")
    assert (info.header_offset + 30 + len(info.filename) + len(info.extra)) % 4 == 0
EOF

echo "6/6 signing"
if [ ! -f "$KEYSTORE" ]; then
  echo "  creating a new debug keystore at $KEYSTORE (keep it: updates need the same key)"
  "$JDK/keytool" -genkeypair -keystore "$KEYSTORE" -storepass android -keypass android \
    -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Android Debug" >/dev/null 2>&1
fi
OUT="$ROOT/build/Pindad-UGV-UAV-$VNAME.apk"
"$JDK/java" -jar "$APKSIGNER_JAR" sign --ks "$KEYSTORE" --ks-pass "pass:${KS_PASS:-android}" \
  --key-pass "pass:${KEY_PASS:-android}" --out "$OUT" "$B/unsigned.apk"
"$JDK/java" -jar "$APKSIGNER_JAR" verify "$OUT"
ls -l "$OUT"
