"""
S3 operations for the intake path (§3.3 step 2, §11.3).

The guarantees this module exists to provide, all of which the specification calls
"kept unchanged" and more rigorous than the original plan required:

* **Magic-byte verification**, not extension trust.
* **Checksum matching** — we compute SHA-256 locally and require S3 to agree.
* **SSE enforced** on every write.
* **S3 version-ID captured**, so a re-run is distinguishable from an overwrite and
  the OCR idempotency key (bottleneck B8) has something stable to hash.
* **Write-once** — the source bucket is versioned with Object Lock in GOVERNANCE
  mode, so a key written wrongly cannot simply be cleaned up.

Nothing here writes to the source bucket except :func:`put_source_document`.
"""

from __future__ import annotations

import base64
import hashlib
import logging

import boto3
from botocore.exceptions import ClientError

from shared.config import get_settings
from shared.s3_keys import assert_not_derived, is_source_key

log = logging.getLogger("cbc.storage")

#: %PDF-1.x. Checked against the file's first bytes rather than its filename:
#: an extension is a claim by the uploader, not evidence.
PDF_MAGIC = b"%PDF-"

#: Refuse anything larger before it reaches S3. Textract's own async ceiling is
#: 500 MB, and a bid set beyond this is a mis-upload rather than a big job (§4.6).
MAX_UPLOAD_BYTES = 500 * 1024 * 1024


class UploadRejected(ValueError):
    """The uploaded bytes are not an acceptable source document."""


def verify_pdf_bytes(data: bytes, *, declared_name: str = "") -> None:
    """
    Reject anything that is not a plausible PDF, before a byte reaches S3.

    Raises :class:`UploadRejected` with a message an estimator can act on.
    """
    if not data:
        raise UploadRejected("the uploaded file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadRejected(
            f"file is {len(data) / 1e6:.0f} MB; the limit is {MAX_UPLOAD_BYTES / 1e6:.0f} MB. "
            "Split the bid set and upload the parts separately."
        )
    if not data.startswith(PDF_MAGIC):
        raise UploadRejected(
            f"{declared_name or 'the file'} is not a PDF: expected a %PDF- header, "
            f"found {data[:8]!r}. The extension is not evidence."
        )
    # A truncated download is the common real-world failure and it produces a file
    # that opens in some viewers and fails in Textract, hours later.
    if b"%%EOF" not in data[-2048:]:
        log.warning("PDF has no %%%%EOF marker in its final 2 KB; it may be truncated")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_b64(data: bytes) -> str:
    """S3's ChecksumSHA256 wants base64 of the raw digest, not hex."""
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


def get_client():
    return boto3.client("s3", **get_settings().boto_kwargs_for("s3"))


def put_source_document(key: str, data: bytes, *, content_type: str = "application/pdf") -> dict:
    """
    Write one source PDF, once, immutably.

    Returns ``{"version_id", "checksum_sha256", "size_bytes", "bucket", "key"}``.

    ``ChecksumSHA256`` is supplied so S3 verifies the payload independently; if the
    bytes were corrupted in transit the ``PutObject`` fails rather than storing a
    damaged document that fails much later inside Textract.
    """
    assert_not_derived(key)
    if not is_source_key(key):
        raise UploadRejected(
            f"refusing to write {key!r} to the source bucket: keys must come from "
            "shared.s3_keys.get_source_document_key"
        )

    settings_obj = get_settings()
    checksum_hex = sha256_hex(data)

    response = get_client().put_object(
        Bucket=settings_obj.s3_source_bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
        # NFR-4: customer drawings are encrypted at rest inside the approved
        # environment. Enforced per object as well as by bucket policy, so a
        # policy change cannot silently downgrade past writes.
        ServerSideEncryption="AES256",
        ChecksumAlgorithm="SHA256",
        ChecksumSHA256=_sha256_b64(data),
        Metadata={"sha256": checksum_hex},
    )

    version_id = response.get("VersionId")
    if not version_id:
        # Without a version-ID the OCR idempotency key has nothing stable to hash,
        # and a re-upload is indistinguishable from an overwrite (bottleneck B8).
        log.warning(
            "source bucket %s returned no VersionId — versioning may be disabled",
            settings_obj.s3_source_bucket,
        )

    log.info(
        "source document written",
        extra={"key": key, "version_id": version_id, "size_bytes": len(data)},
    )
    return {
        "bucket": settings_obj.s3_source_bucket,
        "key": key,
        "version_id": version_id,
        "checksum_sha256": checksum_hex,
        "size_bytes": len(data),
    }


def get_source_document(key: str, version_id: str | None = None) -> bytes:
    """Read a source PDF back. Pinned to a version when one is supplied."""
    kwargs = {"Bucket": get_settings().s3_source_bucket, "Key": key}
    if version_id:
        kwargs["VersionId"] = version_id
    return get_client().get_object(**kwargs)["Body"].read()


def put_derived(key: str, data: bytes, *, content_type: str, content_encoding: str | None = None) -> str:
    """
    Write a derived artefact and return its version-ID.

    Everything preprocessing produces lands here: rasters, split parts, extracted
    text, OCR JSON. The source PDF is never mutated (§4.1 invariant).
    """
    kwargs = {
        "Bucket": get_settings().s3_derived_bucket,
        "Key": key,
        "Body": data,
        "ContentType": content_type,
        "ServerSideEncryption": "AES256",
    }
    if content_encoding:
        kwargs["ContentEncoding"] = content_encoding
    return get_client().put_object(**kwargs).get("VersionId", "")


def get_derived(key: str, version_id: str | None = None) -> bytes:
    kwargs = {"Bucket": get_settings().s3_derived_bucket, "Key": key}
    if version_id:
        kwargs["VersionId"] = version_id
    return get_client().get_object(**kwargs)["Body"].read()


def derived_exists(key: str) -> bool:
    try:
        get_client().head_object(Bucket=get_settings().s3_derived_bucket, Key=key)
        return True
    except ClientError:
        return False


def public_raster_url(key: str) -> str:
    """
    URL the review viewer fetches a page raster from.

    CloudFront when configured, otherwise a direct S3 URL for local development.
    Rasters are pre-rendered and immutable, so they are cache-warm forever and
    invalidation is never needed (§4.5, bottleneck B5).
    """
    settings_obj = get_settings()
    if settings_obj.cloudfront_domain:
        return f"https://{settings_obj.cloudfront_domain}/{key}"
    # The browser has to be able to resolve this, and the endpoint the API talks
    # to locally is a compose service name that it cannot.
    endpoint = settings_obj.public_raster_endpoint_url or settings_obj.aws_endpoint_url
    if endpoint:
        return f"{endpoint}/{settings_obj.s3_derived_bucket}/{key}"
    return f"https://{settings_obj.s3_derived_bucket}.s3.{settings_obj.aws_region}.amazonaws.com/{key}"
