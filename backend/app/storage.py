"""Cloud storage (AWS S3, or Cloudflare R2 through the same S3 API).

The browser uploads and downloads audio *directly* to and from the bucket using
short-lived signed links. Our server only signs the links; heavy audio never
passes through it.
"""
import re
import unicodedata
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import boto3
from boto3.exceptions import S3UploadFailedError
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import get_settings

ALLOWED_TYPES = {                      # file extension -> content type
    "wav": "audio/wav",
    "flac": "audio/flac",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "aiff": "audio/aiff",
    "aif": "audio/aiff",
}
UPLOAD_KEY_PATTERN = re.compile(rf"uploads/[0-9a-f]{{32}}/source\.({'|'.join(ALLOWED_TYPES)})")

# Status codes worth retrying; any other 4xx (access denied, no such bucket...) won't fix itself
_TRANSIENT_HTTP = {408, 429, 500, 502, 503, 504}


class StorageError(RuntimeError):
    """Storage failed. The message is for logs only (it can contain bucket names and keys)."""

    def __init__(self, message: str, transient: bool = True):
        super().__init__(message)
        self.transient = transient


def _wrap(action: str, exc: BaseException) -> StorageError:
    transient = True
    if isinstance(exc, ClientError):
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 500)
        code = exc.response.get("Error", {}).get("Code", "")
        transient = status in _TRANSIENT_HTTP or code in ("SlowDown", "RequestTimeout", "InternalError")
    elif isinstance(exc, S3UploadFailedError):
        cause = exc.__cause__ or exc.__context__
        transient = _wrap(action, cause).transient if isinstance(cause, ClientError) else True
    return StorageError(f"{action}: {exc}", transient=transient)


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    etag: str


# --------------------------------------------------------------------------- #
# Naming files in the bucket
# --------------------------------------------------------------------------- #
def content_type_for(filename: str) -> tuple[str, str]:
    """Return (extension, content type), or raise ValueError for unsupported files."""
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in ALLOWED_TYPES:
        shown = f"'.{ext}'" if ext else "without an extension"
        raise ValueError(f"Unsupported file type {shown}. Allowed: {', '.join(sorted(ALLOWED_TYPES))}")
    return ext, ALLOWED_TYPES[ext]


def new_upload_key(filename: str) -> str:
    ext, _ = content_type_for(filename)
    return f"uploads/{uuid.uuid4().hex}/source.{ext}"


def output_key(job_id: str) -> str:
    return f"outputs/{job_id}/master.wav"


def preview_key(job_id: str, which: str) -> str:
    """Browser-friendly FLAC copies used only for playback ("original" or "enhanced")."""
    if which not in ("original", "enhanced"):
        raise ValueError(f"Unknown preview '{which}'")
    return f"outputs/{job_id}/{which}_preview.flac"


def is_valid_upload_key(key: str) -> bool:
    """Only keys we created ourselves are allowed, so nobody can point a job at other files."""
    return bool(UPLOAD_KEY_PATTERN.fullmatch(key))   # fullmatch: no trailing "\n" tricks


def check_size(size_bytes: int) -> None:
    limit = get_settings().max_upload_bytes
    if size_bytes > limit:
        raise ValueError(f"File is {size_bytes / 1_048_576:.0f} MB; the limit is {limit / 1_048_576:.0f} MB")


def safe_display_name(filename: str, max_length: int = 120) -> str:
    """The user's file name without folders, control characters or quotes (for display and headers)."""
    name = Path(filename.replace("\\", "/")).name
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C" and ch not in '"\\')
    name = name.strip().strip(".") or "audio"
    if len(name) > max_length:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: max_length - len(ext) - 1] + dot + ext) if dot and len(ext) <= 5 else name[:max_length]
    return name


def content_disposition(filename: str, attachment: bool) -> str:
    """RFC 6266 header: an ASCII fallback plus the exact UTF-8 name, with nothing injectable."""
    name = safe_display_name(filename)
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode() or "audio"
    kind = "attachment" if attachment else "inline"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


# --------------------------------------------------------------------------- #
# Talking to the bucket
# --------------------------------------------------------------------------- #
class Storage:
    def __init__(self, client=None):
        s = get_settings()
        self.bucket = s.s3_bucket
        self.s3 = client or boto3.session.Session().client(   # own session: thread-safe to create
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
        except (ClientError, BotoCoreError) as exc:
            raise _wrap(f"Cannot access bucket '{self.bucket}'", exc) from exc

    def presign_upload(self, key: str, content_type: str, size_bytes: int) -> str:
        """A link the browser can PUT one file to. Type and exact size are part of the signature."""
        check_size(size_bytes)
        return self.s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key,
                    "ContentType": content_type, "ContentLength": size_bytes},
            ExpiresIn=get_settings().upload_link_seconds,
            HttpMethod="PUT",
        )

    def presign_download(self, key: str, download_name: str | None = None,
                         attachment: bool = False) -> str:
        """A link the browser can play (inline) or save (attachment) a file from."""
        params = {"Bucket": self.bucket, "Key": key}
        if download_name:
            params["ResponseContentDisposition"] = content_disposition(download_name, attachment)
        return self.s3.generate_presigned_url("get_object", Params=params,
                                              ExpiresIn=get_settings().download_link_seconds)

    def stat(self, key: str) -> ObjectInfo | None:
        """Size and ETag, or None if the file isn't there."""
        try:
            head = self.s3.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise _wrap(f"Could not check '{key}'", exc) from exc
        except BotoCoreError as exc:
            raise _wrap(f"Could not check '{key}'", exc) from exc
        return ObjectInfo(size=int(head["ContentLength"]), etag=str(head.get("ETag", "")).strip('"'))

    def size_of(self, key: str) -> int | None:
        info = self.stat(key)
        return None if info is None else info.size

    def download(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.s3.download_file(self.bucket, key, str(dest))
        except (ClientError, BotoCoreError) as exc:
            raise _wrap(f"Could not download '{key}'", exc) from exc
        return dest

    def upload(self, src: Path, key: str, content_type: str = "audio/wav") -> str:
        try:
            self.s3.upload_file(str(src), self.bucket, key, ExtraArgs={"ContentType": content_type})
        except (ClientError, BotoCoreError, S3UploadFailedError) as exc:
            raise _wrap(f"Could not upload '{key}'", exc) from exc
        return key

    def delete(self, key: str) -> None:
        try:
            self.s3.delete_object(Bucket=self.bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise _wrap(f"Could not delete '{key}'", exc) from exc


@lru_cache(maxsize=1)
def get_storage() -> Storage:
    """One S3 client per process (clients are thread-safe; creating one per request is slow)."""
    return Storage()
