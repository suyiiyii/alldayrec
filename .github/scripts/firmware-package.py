#!/usr/bin/env python3
"""Package built bits only; retain relay flash paths/parameters verbatim."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODELS = {"sdmod": "LR-X1", "promicro": "LR-P1", "promicro-p2": "LR-P2", "promicro-p3": "LR-P3", "expansion": "LR-X1-expansion-test"}


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    family, variant = sys.argv[1:3]
    build = ROOT / "firmware" / family / "build"
    if family == "devkit":
        build /= f"lr-{variant}"
    commit = git("rev-parse", "HEAD")
    stage = Path(os.environ["RUNNER_TEMP"]) / f"firmware-package-{variant}"
    stage.mkdir(parents=True, exist_ok=False)
    toolchain = json.loads(Path(os.environ["FIRMWARE_TOOLCHAIN_METADATA"]).read_text())
    metadata = {"source_commit": commit, "family": family, "variant": variant, "toolchain": toolchain,
                "build_parallelism": 2, "github_run_id": os.environ.get("GITHUB_RUN_ID")}
    manifest = Path(os.environ["FIRMWARE_SDK_MANIFEST"])
    shutil.copy2(manifest, stage / ("west-manifest.yml" if family != "blerelay" else "idf-tools.json"))
    if family != "blerelay":
        shutil.copy2(Path(os.environ["FIRMWARE_SDK_PROJECTS"]), stage / "west-projects.txt")
    if family in ("devkit", "selftest"):
        config = (build / "zephyr/.config").read_text()
        if family == "devkit":
            def value(key):
                match = re.search(rf'^CONFIG_{key}="(.*)"$', config, re.M)
                if not match:
                    raise SystemExit(f"Missing generated CONFIG_{key}")
                return match.group(1)
            model = value("BT_DIS_MODEL")
            expected = "LR-X1" if variant == "expansion" else MODELS[variant]
            if model != expected:
                raise SystemExit(f"Built {model}, expected {expected}")
            version = value("BT_DIS_FW_REV_STR")
            if not version.endswith("-" + git("rev-parse", "--short", "HEAD")):
                raise SystemExit("Generated recorder revision does not identify clean HEAD")
            shutil.copy2(build / f"lr-{variant}.zip", stage / f"{MODELS[variant]}.dfu.zip")
            metadata["dfu"] = {"packager": "adafruit-nrfutil==0.5.3.post16", "dev_type": "0x0052", "sd_req": "0xFFFE"}
        else:
            model = "LR-selftest-ProMicro"
            header = (build / "zephyr/include/generated/version.h").read_text()
            match = re.search(r'^#define BUILD_VERSION[ \t]+([0-9]{8}\.[0-9]{6}-[0-9a-f]+)[ \t]*$', header, re.M)
            if not match:
                raise SystemExit("Missing generated selftest BUILD_VERSION")
            version = match.group(1)
            expected = git("log", "-1", "--format=%cd", "--date=format:%Y%m%d.%H%M%S") + "-" + git("rev-parse", "--short", "HEAD")
            if version != expected:
                raise SystemExit("Generated selftest version differs from the built source commit")
            shutil.copy2(build / "zephyr/include/generated/version.h", stage / "version.h")
        for ext in ("uf2", "hex"):
            shutil.copy2(build / f"zephyr/zephyr.{ext}", stage / f"{model}.{ext}")
        shutil.copy2(build / "zephyr/.config", stage / "zephyr.config")
        metadata.update(model=model, firmware_version=version, board="xiao_ble/nrf52840" + ("/sense" if family == "devkit" else ""))
    else:
        flash = json.loads((build / "flasher_args.json").read_text())
        description = json.loads((build / "project_description.json").read_text())
        version = description["project_version"]
        model = "LR-blerelay-ESP32S3-CI-TEST-KEY"
        # flash_files covers bootloader, partition table, application and any other required images.
        for relative in flash["flash_files"].values():
            source = (build / relative).resolve()
            source.relative_to(build.resolve())
            destination = stage / source.relative_to(build.resolve())
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        for name in ("flasher_args.json", "flash_args", "project_description.json"):
            shutil.copy2(build / name, stage / name)
        shutil.copy2(ROOT / "firmware/blerelay/sdkconfig", stage / "sdkconfig")
        metadata.update(model=model, firmware_version=version, board="Seeed XIAO ESP32-S3", target="esp32s3",
                        relay_key_mode="public-ci-test-key-embedded")
        (stage / "FLASHING.txt").write_text(
            "From this extracted directory with ESP-IDF v5.5.5 exported:\n"
            "python -m esptool --chip esp32s3 --port PORT --before default_reset --after hard_reset write_flash @flash_args\n"
            "flasher_args.json preserves the exact offsets, settings and filenames from this build.\n"
            "TEST ONLY: this relay embeds the PUBLIC CI authentication key (64 zero hex digits). Never use on production devices.\n")
    (stage / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    files = sorted(p for p in stage.rglob("*") if p.is_file())
    (stage / "SHA256SUMS").write_text("".join(f"{sha(p)}  {p.relative_to(stage).as_posix()}\n" for p in files))
    out = ROOT / "firmware-artifacts"
    out.mkdir(exist_ok=True)
    label = MODELS[variant] if family == "devkit" else model
    archive = out / f"{label}-{version}-{commit[:12]}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(stage, arcname=archive.name.removesuffix(".tar.gz"))
    archive.with_name(archive.name + ".sha256").write_text(f"{sha(archive)}  {archive.name}\n")
    print(f"Packaged {archive.name}")


if __name__ == "__main__":
    main()
