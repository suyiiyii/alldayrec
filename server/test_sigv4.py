"""sigv4.sign against the worked examples of the S3 API Reference's "Authenticating Requests: Using the Authorization
Header (AWS Signature Version 4)" (sig-v4-header-based-auth.html): its example keys, time and expected signatures."""
import hashlib

import pytest

from alldayrec import sigv4

KEY, SECRET = "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
HOST, DATE = "examplebucket.s3.amazonaws.com", "20130524T000000Z"


@pytest.mark.parametrize("method, path, query, headers, payload, signed, signature", [
    ("GET", "/test.txt", {}, {"Range": "bytes=0-9"}, b"",  # GET Object
     "host;range;x-amz-content-sha256;x-amz-date", "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"),
    ("PUT", "/test$file.text", {}, {"Date": "Fri, 24 May 2013 00:00:00 GMT", "x-amz-storage-class": "REDUCED_REDUNDANCY"},  # PUT Object
     b"Welcome to Amazon S3.", "date;host;x-amz-content-sha256;x-amz-date;x-amz-storage-class",
     "98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd"),
    ("GET", "/", {"lifecycle": ""}, {}, b"",  # GET Bucket Lifecycle
     "host;x-amz-content-sha256;x-amz-date", "fea454ca298b7da1c68078a5d1bdbfbbe0d65c699e0f91ac7a200a0136783543"),
    ("GET", "/", {"max-keys": "2", "prefix": "J"}, {}, b"",  # Get Bucket (List Objects)
     "host;x-amz-content-sha256;x-amz-date", "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"),
])
def test_aws_examples(method, path, query, headers, payload, signed, signature):
    h = sigv4.sign(method, HOST, path, query, headers, hashlib.sha256(payload).hexdigest(), KEY, SECRET, "us-east-1", DATE)
    assert h["authorization"] == (f"AWS4-HMAC-SHA256 Credential={KEY}/20130524/us-east-1/s3/aws4_request,"
                                  f"SignedHeaders={signed},Signature={signature}")
