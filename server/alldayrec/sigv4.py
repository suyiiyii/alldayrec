"""AWS Signature Version 4 for S3 requests: the Authorization header, the payload signed by its SHA-256 (never
UNSIGNED-PAYLOAD), path-style URLs (store.S3 signs /<bucket>/<key>).

Follows the Amazon S3 API Reference, "Authenticating Requests: Using the Authorization Header (AWS Signature Version 4)"
and its "Signature Calculations for the Authorization Header: Transferring Payload in a Single Chunk":
https://docs.aws.amazon.com/AmazonS3/latest/API/sig-v4-header-based-auth.html
test_sigv4.py checks it against that page's worked examples.
"""
import hashlib
import hmac
from urllib.parse import quote

EMPTY = hashlib.sha256(b"").hexdigest()


def _uri(s: str, slash: bool = False) -> str:
    """UriEncode(): every byte but A-Z a-z 0-9 - . _ ~ as %XY (and / kept in a path)."""
    return quote(s, safe="/~" if slash else "~")


def sign(method: str, host: str, path: str, query: dict, headers: dict, payload_sha256: str,
         access_key: str, secret_key: str, region: str, amz_date: str) -> dict:
    """headers with host, x-amz-date (amz_date: YYYYMMDD'T'HHMMSS'Z', UTC), x-amz-content-sha256 and Authorization
    added, every one of them signed. path: the URI path, not yet encoded."""
    h = {k.lower(): " ".join(str(v).split()) for k, v in headers.items()}  # trimmed, inner runs of spaces as one
    h |= {"host": host, "x-amz-date": amz_date, "x-amz-content-sha256": payload_sha256}
    names = sorted(h)
    canonical = "\n".join([
        method, _uri(path, slash=True),
        "&".join(f"{_uri(k)}={_uri(v)}" for k, v in sorted(query.items())),
        "".join(f"{k}:{h[k]}\n" for k in names), ";".join(names), payload_sha256])
    day = amz_date[:8]
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = ("AWS4" + secret_key).encode()
    for part in (day, region, "s3", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    h["authorization"] = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope},SignedHeaders={';'.join(names)},Signature={signature}"
    return h
