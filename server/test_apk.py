"""apk.py: the version /api/app/latest gives is the one in the APK's binary manifest."""
import base64
import zipfile

import pytest

from alldayrec import apk

# aapt2 link of <manifest package="x.y" android:versionCode="18" android:versionName="1.4.3"/>
MANIFEST = base64.b64decode(
    "AwAIABADAAABABwA+AEAAA0AAAAAAAAAAAAAAFAAAAAAAAAAAAAAABoAAAA0AAAAWgAAAJAAAACeAAAApgAAALgAAAAQAQAAJAEAADYBAABqAQAA"
    "ngEAAAsAdgBlAHIAcwBpAG8AbgBDAG8AZABlAAAACwB2AGUAcgBzAGkAbwBuAE4AYQBtAGUAAAARAGMAbwBtAHAAaQBsAGUAUwBkAGsAVgBlAHIA"
    "cwBpAG8AbgAAABkAYwBvAG0AcABpAGwAZQBTAGQAawBWAGUAcgBzAGkAbwBuAEMAbwBkAGUAbgBhAG0AZQAAAAUAMQAuADQALgAzAAAAAgAxADcA"
    "AAAHAGEAbgBkAHIAbwBpAGQAAAAqAGgAdAB0AHAAOgAvAC8AcwBjAGgAZQBtAGEAcwAuAGEAbgBkAHIAbwBpAGQALgBjAG8AbQAvAGEAcABrAC8A"
    "cgBlAHMALwBhAG4AZAByAG8AaQBkAAAACABtAGEAbgBpAGYAZQBzAHQAAAAHAHAAYQBjAGsAYQBnAGUAAAAYAHAAbABhAHQAZgBvAHIAbQBCAHUA"
    "aQBsAGQAVgBlAHIAcwBpAG8AbgBDAG8AZABlAAAAGABwAGwAYQB0AGYAbwByAG0AQgB1AGkAbABkAFYAZQByAHMAaQBvAG4ATgBhAG0AZQAAAAMA"
    "eAAuAHkAAACAAQgAGAAAABsCAQEcAgEBcgUBAXMFAQEAARAAGAAAAAEAAAD/////BgAAAAcAAAACARAAsAAAAAEAAAD//////////wgAAAAUABQA"
    "BwAAAAAAAAAHAAAAAAAAAP////8IAAAQEgAAAAcAAAABAAAABAAAAAgAAAMEAAAABwAAAAIAAAD/////CAAAECUAAAAHAAAAAwAAAAUAAAAIAAAD"
    "BQAAAP////8JAAAADAAAAAgAAAMMAAAA/////woAAAD/////CAAAECUAAAD/////CwAAAP////8IAAAQEQAAAAMBEAAYAAAAAQAAAP//////////"
    "CAAAAAEBEAAYAAAAAQAAAP////8GAAAABwAAAA=="
)


def test_version(tmp_path):
    p = tmp_path / "a.apk"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AndroidManifest.xml", MANIFEST)
    assert apk.version(p) == {"versionName": "1.4.3", "versionCode": 18}
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("AndroidManifest.xml", MANIFEST.replace(b"v\x00e\x00r\x00s\x00i\x00o\x00n\x00C", b"v\x00e\x00r\x00s\x00i\x00o\x00n\x00X"))
    with pytest.raises(ValueError):
        apk.version(p)
