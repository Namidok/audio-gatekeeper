"""Step 19: cloud storage (AWS S3, or Cloudflare R2 through the same S3 API).

The browser uploads and downloads audio *directly* to and from the bucket using
short-lived signed links. Our server only signs the links; heavy audio never
passes through it.
"""
import re
import uuid
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import get_settings

UPLOAD_LINK_SECONDS = 15 * 60          # an upload link works for 15 minutes
DOWNLOAD_LINK_SECONDS = 60 * 60        # a playback/download link works for 1 hour

ALLOWED_TYPES = {                      # file extension -> content type
    "wav": "audio/wav",
    "flac": "audio/flac",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "aiff": "audio/aiff",
    "aif": "audio/aiff",
}
UPLOAD_KEY_PATTERN = re.compile(r"^uploads/[0-9a-f]{32}/source\.(wav|flac|mp3|ogg|aiff|aif)$")


class StorageError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Naming files in the bucket
# --------------------------------------------------------------------------- #
def content_type_for(filename: str) -> tuple[str, str]:
    """Return (extension, content type), or raise ValueError for unsupported files."""
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in ALLOWED_TYPES:
        raise ValueError(f"Unsupported file type '.{ext}'. Allowed: {', '.join(sorted(ALLOWED_TYPES))}")
    return ext, ALLOWED_TYPES[ext]


def new_upload_key(filename: str) -> str:
    ext, _ = content_type_for(filename)
    return f"uploads/{uuid.uuid4().hex}/source.{ext}"


def output_key(job_id: str) -> str:
    return f"outputs/{job_id}/master.wav"


def is_valid_upload_key(key: str) -> bool:
    """Only keys we created ourselves are allowed, so nobody can point a job at other files."""
    return bool(UPLOAD_KEY_PATTERN.match(key))


# --------------------------------------------------------------------------- #
# Talking to the bucket
# --------------------------------------------------------------------------- #
class Storage:
    def __init__(self, client=None):
        s = get_settings()
        self.bucket = s.s3_bucket
        self.s3 = client or boto3.client(
            "s3",
            region_name=s.s3_region,
            # Regional endpoint: avoids redirects that break browser uploads
            endpoint_url=s.s3_endpoint_url or f"https://s3.{s.s3_region}.amazonaws.com",
            aws_access_key_id=s.s3_access_key_id or None,
            aws_secret_access_key=s.s3_secret_access_key or None,
            config=Config(
                signature_version="s3v4",
                # AWS: bucket name in the host (my-bucket.s3...). R2/others: let boto3 decide
                s3={"addressing_style": "auto" if s.s3_endpoint_url else "virtual"},
                retries={"max_attempts": 5, "mode": "standard"},
            ),
        )

    def check_access(self) -> None:
        try:
            self.s3.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            raise StorageError(f"Cannot access bucket '{self.bucket}': {exc}") from exc

    def presign_upload(self, key: str, content_type: str, size_bytes: int) -> str:
        """A link the browser can PUT one file to. Type and exact size are locked in."""
        limit = get_settings().max_upload_bytes
        if size_bytes > limit:
            raise ValueError(f"File is {size_bytes / 1e6:.0f} MB; the limit is {limit / 1e6:.0f} MB")
        return self.s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key,
                    "ContentType": content_type, "ContentLength": size_bytes},
            ExpiresIn=UPLOAD_LINK_SECONDS,
            HttpMethod="PUT",
        )

    def presign_download(self, key: str, download_name: str | None = None) -> str:
        """A link the browser can play or download a file from."""
        params = {"Bucket": self.bucket, "Key": key}
        if download_name:
            params["ResponseContentDisposition"] = f'inline; filename="{download_name}"'
        return self.s3.generate_presigned_url("get_object", Params=params,
                                              ExpiresIn=DOWNLOAD_LINK_SECONDS)

    def size_of(self, key: str) -> int | None:
        """File size in bytes, or None if the file isn't there."""
        try:
            return self.s3.head_object(Bucket=self.bucket, Key=key)["ContentLength"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return None
            raise StorageError(f"Could not check '{key}': {exc}") from exc

    def download(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.s3.download_file(self.bucket, key, str(dest))
        except ClientError as exc:
            raise StorageError(f"Could not download '{key}': {exc}") from exc
        return dest

    def upload(self, src: Path, key: str, content_type: str = "audio/wav") -> str:
        try:
            self.s3.upload_file(str(src), self.bucket, key, ExtraArgs={"ContentType": content_type})
        except ClientError as exc:
            raise StorageError(f"Could not upload '{key}': {exc}") from exc
        return key

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=key)
