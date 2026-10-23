#!/usr/bin/env bash
set -euo pipefail

flutter_version=3.47.5
flutter_commit=6a19cca56475dbfba1478ee68d7bd0c2ef891da1
flutter_root="$RUNNER_TEMP/apk-flutter"
sdk_root="$RUNNER_TEMP/apk-android"
git clone --quiet --depth 1 --branch "$flutter_version" https://github.com/flutter/flutter.git "$flutter_root"
[[ $(git -C "$flutter_root" rev-parse HEAD) == "$flutter_commit" ]] || {
  echo "Flutter must be the official $flutter_version commit $flutter_commit" >&2
  exit 1
}
export PATH="$flutter_root/bin:$HOME/.local/bin:$PATH"
export ANDROID_HOME="$sdk_root" ANDROID_SDK_ROOT="$sdk_root"
mkdir -p "$sdk_root"
[[ -O "$sdk_root" && -w "$sdk_root" ]] || {
  echo 'Android SDK must be owned and writable by the invoking runner.' >&2
  exit 1
}
# Gradle builds Flutter's plugin in place; one root-run build leaves files there the runner cannot replace.
[[ -z $(find "$flutter_root/packages/flutter_tools/gradle" ! -writable -print -quit) ]] || {
  echo "Flutter's packages/flutter_tools/gradle must be writable by the invoking runner." >&2
  exit 1
}

# Official command-line tools 22.0; SHA256 published on developer.android.com/studio.
cmd_version=22.0
cmd_archive=commandlinetools-linux-15859902_latest.zip
cmd_sha256=4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583
cmd_root="$sdk_root/cmdline-tools/$cmd_version"
# Reuse an existing official 22.0 installation, including local smoke-build SDKs.
if [[ -x "$sdk_root/cmdline-tools/latest/bin/sdkmanager" ]] &&
   [[ $("$sdk_root/cmdline-tools/latest/bin/sdkmanager" --version) == "$cmd_version" ]]; then
  cmd_root="$sdk_root/cmdline-tools/latest"
fi
if [[ ! -x "$cmd_root/bin/sdkmanager" ]]; then
  staging=$(mktemp -d "$RUNNER_TEMP/apk-sdk.XXXXXX")
  trap 'rm -rf "$staging"' EXIT
  curl --fail --location --silent --show-error "https://dl.google.com/android/repository/$cmd_archive" --output "$staging/tools.zip"
  printf '%s  %s\n' "$cmd_sha256" "$staging/tools.zip" | sha256sum --check
  unzip -q "$staging/tools.zip" -d "$staging"
  mkdir -p "$sdk_root/cmdline-tools"
  mv "$staging/cmdline-tools" "$cmd_root"
fi
[[ $("$cmd_root/bin/sdkmanager" --version) == "$cmd_version" ]]
# Match the exact Flutter constant used by app/android/app/build.gradle.kts.
ndk_version=$(uv run --no-project --python 3.11 python - "$flutter_root" <<'PY'
import pathlib, re, sys
source = pathlib.Path(sys.argv[1]) / 'packages/flutter_tools/gradle/src/main/kotlin/FlutterExtension.kt'
match = re.search(r'val ndkVersion: String = "([0-9.]+)"', source.read_text())
if not match:
    raise SystemExit('Cannot read the pinned Flutter NDK constant')
print(match[1])
PY
)
# `yes` receives SIGPIPE after sdkmanager finishes; preserve sdkmanager's status.
set +o pipefail
yes | "$cmd_root/bin/sdkmanager" --sdk_root="$sdk_root" --licenses > "$RUNNER_TEMP/apk-sdk-licenses.log" 2>&1
license_status=${PIPESTATUS[1]}
set -o pipefail
[[ $license_status == 0 ]] || { cat "$RUNNER_TEMP/apk-sdk-licenses.log"; exit "$license_status"; }
# Plugins use Flutter's API 36 default; the app uses API 37.0. AGP 9.1.0 requires Build Tools 36.0.0.
# The NDK too for app.yml's unit tests: build.gradle.kts names flutter.ndkVersion, so AGP installs it while configuring.
"$cmd_root/bin/sdkmanager" --sdk_root="$sdk_root" 'platforms;android-36' 'platforms;android-37.0' 'build-tools;36.0.0' "ndk;$ndk_version"
flutter config --no-analytics --android-sdk "$sdk_root"
flutter precache --android
gradle_home="$RUNNER_TEMP/apk-gradle-${GITHUB_RUN_ID:?}-${GITHUB_RUN_ATTEMPT:?}"
{
  echo "FLUTTER_ROOT=$flutter_root"
  echo "ANDROID_HOME=$sdk_root"
  echo "ANDROID_SDK_ROOT=$sdk_root"
  echo "APK_NDK_VERSION=$ndk_version"
  echo "APK_CMDLINE_TOOLS=$cmd_version"
  echo "GRADLE_USER_HOME=$gradle_home"
} >> "$GITHUB_ENV"
echo "$flutter_root/bin" >> "$GITHUB_PATH"
