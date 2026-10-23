#!/usr/bin/env python3
"""Build one APK with an explicit CI signer and archive the exact verified bits."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
SECRET_NAMES = (
    "ANDROID_KEYSTORE_BASE64", "ANDROID_KEYSTORE_PASSWORD", "ANDROID_KEY_ALIAS",
    "ANDROID_KEY_PASSWORD", "ANDROID_SIGNING_CERT_SHA256",
)


def capture(command, **kwargs):
    return subprocess.check_output(command, text=True, **kwargs).strip()


def fingerprint(value):
    value = value.replace(":", "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise SystemExit("Signing certificate fingerprint must be a SHA256 digest")
    return value


def context():
    match = re.search(r"^version:\s*([0-9]+\.[0-9]+\.[0-9]+)\+([0-9]+)\s*$", (APP / "pubspec.yaml").read_text(), re.M)
    if not match:
        raise SystemExit("pubspec.yaml must contain an explicit versionName+versionCode")
    version, code = match.groups()
    commit = capture(["git", "rev-parse", "HEAD"], cwd=ROOT)
    formal = os.environ.get("GITHUB_EVENT_NAME") == "push" and os.environ.get("GITHUB_REF", "").startswith("refs/tags/apk-v")
    tag = os.environ.get("GITHUB_REF_NAME", "") if formal else None
    if formal:
        if tag != f"apk-v{version}":
            raise SystemExit("APK tag must be apk-v followed by the pubspec versionName (no build-number suffix)")
        subprocess.run(["git", "merge-base", "--is-ancestor", commit, "origin/main"], cwd=ROOT, check=True)
        if any(not os.environ.get(name) for name in SECRET_NAMES):
            raise SystemExit("Formal APK release requires all five ANDROID signing secrets; no test-signing fallback")
        fingerprint(os.environ["ANDROID_SIGNING_CERT_SHA256"])
    return version, code, commit, formal, tag


def quiet_keytool(arguments, env):
    result = subprocess.run(["keytool", *arguments], env=env, capture_output=True)
    if result.returncode:
        raise SystemExit("Keystore operation failed; check the supplied key, alias and passwords")
    return result.stdout


def build():
    version, code, commit, formal, tag = context()
    env = os.environ.copy()
    env.pop("ANDROID_KEYSTORE_BASE64", None)
    env.pop("ANDROID_SIGNING_CERT_SHA256", None)
    # System properties override the repository's local 8GiB Gradle defaults.
    env["GRADLE_OPTS"] = (
        '-Dorg.gradle.jvmargs="-Xmx1536m -XX:MaxMetaspaceSize=512m -XX:ReservedCodeCacheSize=128m" '
        '-Dorg.gradle.workers.max=2 -Dorg.gradle.parallel=false -Dorg.gradle.daemon=false '
        '-Dorg.gradle.vfs.watch=false -Dorg.gradle.project.kotlin.compiler.execution.strategy=in-process'
    )
    env["LR_ANDROID_SIGNING_MODE"] = "release" if formal else "test"
    apk = APP / "build/app/outputs/flutter-apk/app-release.apk"
    apk.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="apk-signing-", dir=os.environ["RUNNER_TEMP"]) as private:
        keystore = Path(private) / "signer.keystore"
        env["LR_ANDROID_KEYSTORE_PATH"] = str(keystore)
        if formal:
            try:
                decoded = base64.b64decode("".join(os.environ["ANDROID_KEYSTORE_BASE64"].split()), validate=True)
            except ValueError:
                raise SystemExit("ANDROID_KEYSTORE_BASE64 is not valid base64") from None
            keystore.write_bytes(decoded)
            keystore.chmod(0o600)
            del decoded
        else:
            env.update(ANDROID_KEYSTORE_PASSWORD="android", ANDROID_KEY_ALIAS="live-record-ci-test", ANDROID_KEY_PASSWORD="android")
            quiet_keytool([
                "-genkeypair", "-noprompt", "-keystore", str(keystore),
                "-storepass:env", "ANDROID_KEYSTORE_PASSWORD", "-keypass:env", "ANDROID_KEY_PASSWORD",
                "-alias", env["ANDROID_KEY_ALIAS"], "-keyalg", "RSA", "-keysize", "2048",
                "-validity", "36500", "-dname", "CN=Live Record CI TEST ONLY",
            ], env)
        certificate = quiet_keytool([
            "-exportcert", "-keystore", str(keystore), "-alias", env["ANDROID_KEY_ALIAS"],
            "-storepass:env", "ANDROID_KEYSTORE_PASSWORD",
        ], env)
        cert_sha256 = hashlib.sha256(certificate).hexdigest()
        if formal and cert_sha256 != fingerprint(os.environ["ANDROID_SIGNING_CERT_SHA256"]):
            raise SystemExit("Original signing certificate SHA256 does not match ANDROID_SIGNING_CERT_SHA256")
        subprocess.run(["flutter", "pub", "get", "--enforce-lockfile"], cwd=APP, env=env, check=True)
        subprocess.run(["flutter", "build", "apk", "--release", "--no-pub", "--no-android-gradle-daemon"], cwd=APP, env=env, check=True)
        tools = Path(os.environ["ANDROID_HOME"]) / "build-tools/36.0.0"
        verify = capture([str(tools / "apksigner"), "verify", "--verbose", "--print-certs", str(apk)], env=env)
        signers = re.findall(r"Signer #[0-9]+ certificate SHA-256 digest: ([0-9a-fA-F]+)", verify)
        if signers != [cert_sha256]:
            raise SystemExit("Built APK is not signed by the selected certificate")
        badging = capture([str(tools / "aapt"), "dump", "badging", str(apk)], env=env)
        if f"versionName='{version}'" not in badging or f"versionCode='{int(code)}'" not in badging:
            raise SystemExit("Built APK version differs from pubspec.yaml")
        output = ROOT / "dist/apk"
        if output.exists():
            shutil.rmtree(output)
        output.mkdir(parents=True, exist_ok=True)
        kind = "original-signed" if formal else "TEST-ONLY"
        stem = f"alldayrec-android-v{version}+{code}-{kind}-{commit[:12]}"
        packaged = output / f"{stem}.apk"
        shutil.copyfile(apk, packaged)
        flutter = json.loads(capture(["flutter", "--version", "--machine"], env=env))
        wrapper_properties = (APP / "android/gradle/wrapper/gradle-wrapper.properties").read_text()
        metadata = {
            "source_commit": commit, "version_name": version, "version_code": int(code),
            "model": "android-universal", "release_tag": tag, "signing": kind,
            "signing_certificate_sha256": cert_sha256, "apk": packaged.name,
            "toolchain": {
                "flutter": flutter["frameworkVersion"], "flutter_commit": flutter["frameworkRevision"],
                "dart": flutter["dartSdkVersion"], "android_api": "37.0", "android_plugin_api": "36",
                "build_tools": "36.0.0", "ndk": os.environ["APK_NDK_VERSION"],
                "android_commandline_tools": os.environ["APK_CMDLINE_TOOLS"],
                "agp": "9.1.0", "gradle_distribution": next(line.split("=", 1)[1] for line in wrapper_properties.splitlines() if line.startswith("distributionUrl=")),
                "java": capture(["java", "-version"], stderr=subprocess.STDOUT, env=env),
            },
        }
        metadata_path = output / f"{stem}.json"
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
        notes = output / "release-notes.txt"
        notes.write_text(f"APK {version}+{code}\nSource commit: {commit}\nModel: android-universal\nSigning: {kind}\nCertificate SHA256: {cert_sha256}\n\nBuilt and signed by GitHub Actions run {os.environ.get('GITHUB_RUN_ID', '')}.\n")
        checksums = []
        for path in (packaged, metadata_path, notes):
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            checksums.append(f"{digest}  {path.name}\n")
        (output / "SHA256SUMS").write_text("".join(checksums))
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
                stream.write(f"commit={commit}\n")


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        context()
    elif not sys.argv[1:]:
        build()
    else:
        raise SystemExit("Usage: apk-build.py [--check]")
