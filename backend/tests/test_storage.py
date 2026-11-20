"""S3 storage (moto): key validation, signed links, upload/download."""
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from app.storage import (
    StorageError,
    content_disposition,
    content_type_for,
    is_valid_upload_key,
    new_upload_key,
    preview_key,
    safe_display_name,
)

VALID = "uploads/0123456789abcdef0123456789abcdef/source.wav"


# ---------- key validation ----------
def test_new_keys_are_valid():
    assert is_valid_upload_key(new_upload_key("Song.MP3"))


@pytest.mark.parametrize("key", [
    VALID + "\n",                                            # trailing newline (audit finding)
    "uploads/../outputs/x/master.wav",                       # path traversal
    "outputs/0123456789abcdef0123456789abcdef/master.wav",   # someone else's output
    "uploads/0123456789ABCDEF0123456789ABCDEF/source.wav",   # not our format
    "uploads/0123456789abcdef0123456789abcdef/source.exe",
    "/" + VALID,
])
def test_foreign_or_malformed_keys_are_rejected(key):
    assert not is_valid_upload_key(key)


def test_valid_key_is_accepted():
    assert is_valid_upload_key(VALID)


def test_content_type_rules():
    assert content_type_for("A.FlAc") == ("flac", "audio/flac")
    with pytest.raises(ValueError):
        content_type_for("virus.exe")
    with pytest.raises(ValueError):
        content_type_for("no_extension")


# ---------- file names in headers ----------
@pytest.mark.parametrize("raw", ['evil".wav\r\nX-Injected: 1', "a\\b/c\x00.wav", "../../etc/passwd"])
def test_display_name_strips_dangerous_characters(raw):
    name = safe_display_name(raw)
    assert not any(c in name for c in '"\r\n\x00\\/')


def test_content_disposition_is_rfc6266_with_utf8_name():
    header = content_disposition("Beyoncé – Halo.wav", attachment=True)
    assert header.startswith('attachment; filename="')
    assert "filename*=UTF-8''" in header
    assert unquote(header.split("filename*=UTF-8''")[1]) == "Beyoncé – Halo.wav"
    assert all(ord(c) < 128 for c in header)


# ---------- signed links ----------
def test_presigned_upload_locks_type_size_and_expiry(storage, settings):
    url = storage.presign_upload(VALID, "audio/wav", 1234)
    q = parse_qs(urlparse(url).query)
    assert q["X-Amz-SignedHeaders"] == ["content-length;content-type;host"]
    assert q["X-Amz-Expires"] == [str(settings.upload_link_seconds)]
    assert urlparse(url).hostname == "test-bucket.s3.us-east-1.amazonaws.com"


def test_presigned_upload_size_limit(storage, settings):
    storage.presign_upload(VALID, "audio/wav", settings.max_upload_bytes)        # exactly the limit: ok
    with pytest.raises(ValueError):
        storage.presign_upload(VALID, "audio/wav", settings.max_upload_bytes + 1)


def test_presigned_download_attachment_vs_inline(storage, settings):
    inline = parse_qs(urlparse(storage.presign_download(VALID, "a.wav")).query)
    attach = parse_qs(urlparse(storage.presign_download(VALID, "a.wav", attachment=True)).query)
    assert inline["response-content-disposition"][0].startswith("inline;")
    assert attach["response-content-disposition"][0].startswith("attachment;")
    assert inline["X-Amz-Expires"] == [str(settings.download_link_seconds)]


# ---------- talking to the bucket ----------
def test_stat_missing_returns_none(storage):
    assert storage.stat(VALID) is None and storage.size_of(VALID) is None


def test_upload_stat_download_roundtrip(storage, tmp_path):
    src = tmp_path / "in.wav"
    src.write_bytes(b"RIFF" + b"\0" * 100)
    key = preview_key("a" * 32, "original")
    storage.upload(src, key, "audio/flac")
    info = storage.stat(key)
    assert info.size == 104 and info.etag
    out = storage.download(key, tmp_path / "out" / "x.wav")
    assert out.read_bytes() == src.read_bytes()


def test_download_missing_raises_storage_error(storage, tmp_path):
    with pytest.raises(StorageError):
        storage.download(VALID, tmp_path / "x.wav")


def test_upload_to_missing_bucket_is_a_permanent_storage_error(storage, tmp_path, s3):
    s3.delete_bucket(Bucket="test-bucket")
    src = tmp_path / "in.wav"
    src.write_bytes(b"x")
    with pytest.raises(StorageError) as err:
        storage.upload(src, "outputs/x/master.wav")
    assert err.value.transient is False                    # NoSuchBucket won't fix itself
