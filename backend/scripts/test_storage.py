"""Tests the storage layer against your real bucket, exactly the way a browser will use it."""
import urllib.error
import urllib.request
from pathlib import Path

from app.storage import Storage, content_type_for, is_valid_upload_key, new_upload_key, output_key

store = Storage()
store.check_access()
print(f"1. Bucket '{store.bucket}' is reachable")

# 2. The server makes an upload link (this is what the API will do)
source = Path("bad.mp3")
ext, content_type = content_type_for(source.name)
key = new_upload_key(source.name)
data = source.read_bytes()
upload_url = store.presign_upload(key, content_type, len(data))
print(f"2. Upload link created for {key}")

# 3. The "browser" uploads straight to the bucket with that link
request = urllib.request.Request(upload_url, data=data, method="PUT", headers={"Content-Type": content_type})
with urllib.request.urlopen(request) as response:
    print(f"3. Direct upload to the bucket: HTTP {response.status}")
print(f"   File in bucket: {store.size_of(key)} bytes (local file: {len(data)} bytes)")

# 4. Someone tries to misuse the link with a different file type: S3 refuses
bad = urllib.request.Request(upload_url, data=data, method="PUT", headers={"Content-Type": "text/html"})
try:
    urllib.request.urlopen(bad)
    print("4. WARNING: tampered upload was accepted (expected only on a fake S3, never on AWS)")
except urllib.error.HTTPError as exc:
    print(f"4. Tampered upload (wrong type) refused: HTTP {exc.code}")

# 5. The worker downloads it, and uploads a result
copy = store.download(key, Path("storage/test_download.mp3"))
print(f"5. Worker download identical: {copy.read_bytes() == data}")
out_key = output_key("0" * 32)
store.upload(Path("full_band.wav"), out_key)
print(f"   Worker upload: {out_key} ({store.size_of(out_key)} bytes)")

# 6. The "browser" plays the result through a download link
with urllib.request.urlopen(store.presign_download(out_key, "my_song_mastered.wav")) as response:
    played = response.read()
print(f"6. Playback link works: {played == Path('full_band.wav').read_bytes()}")

# 7. Safety checks on names
print(f"7. Our own key accepted: {is_valid_upload_key(key)}   "
      f"someone else's file rejected: {not is_valid_upload_key('outputs/other/master.wav')}")
try:
    content_type_for("virus.exe")
except ValueError as exc:
    print(f"   {exc}")

# Clean up the test files
for k in (key, out_key):
    store.delete(k)
copy.unlink()
print(f"8. Cleaned up. Test file still there? {store.size_of(key) is not None}")
