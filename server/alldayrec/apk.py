"""The Android app's version, read from the APK itself (its binary AndroidManifest.xml): the file /app.apk serves is
the only place it is kept, so it cannot disagree with what a phone downloads.
    uv run python -m alldayrec.apk <apk>     # prints <versionName>+<versionCode>, as pubspec.yaml writes it"""
import struct
import sys
import zipfile


def version(path) -> dict:
    """{"versionName", "versionCode"} of the <manifest> element; ValueError when the manifest is not one aapt2 writes."""
    b = zipfile.ZipFile(path).read("AndroidManifest.xml")
    strings, at = [], 8  # chunks after the file's own header: the string pool, then elements
    while at < len(b):
        kind, head, size = struct.unpack_from("<HHI", b, at)
        if kind == 0x0001:  # string pool
            n, _, flags, start = struct.unpack_from("<IIII", b, at + 8)
            if flags & 0x100:  # ponytail: UTF-16 only, as aapt2 writes manifests; a UTF-8 pool fails here (apk_release.sh runs this first)
                raise ValueError("UTF-8 string pool")
            for off in struct.unpack_from(f"<{n}I", b, at + head):
                p = at + start + off
                ln = struct.unpack_from("<H", b, p)[0]
                if ln & 0x8000:
                    ln, p = ((ln & 0x7fff) << 16) | struct.unpack_from("<H", b, p + 2)[0], p + 2
                strings.append(b[p + 2:p + 2 + 2 * ln].decode("utf-16-le"))
        elif kind == 0x0102:  # an element's start: the first one is <manifest>
            _, name, attr_start, attr_size, count = struct.unpack_from("<IIHHH", b, at + head)
            if strings[name] != "manifest":
                break
            attrs = {}
            for i in range(count):
                _, aname, raw, _, _, typ, data = struct.unpack_from("<IIIHBBI", b, at + head + attr_start + i * attr_size)
                attrs[strings[aname]] = strings[raw] if raw != 0xFFFFFFFF else data if typ == 0x10 else None  # 0x10: decimal int
            if isinstance(attrs.get("versionCode"), int) and isinstance(attrs.get("versionName"), str):
                return {"versionName": attrs["versionName"], "versionCode": attrs["versionCode"]}
            break
        at += size
    raise ValueError("no versionCode/versionName on <manifest>")


if __name__ == "__main__":
    v = version(sys.argv[1])
    print(f"{v['versionName']}+{v['versionCode']}")
