"""Download the fixed, original Xiph RNNoise weights; verify before use."""
import hashlib
import urllib.request
from pathlib import Path

REV = "0fda24c46d78f0207d820bb970fbe85c1971b39c"
SHA = "6b8943dc4a9b6b24425873992a44f29c0577503276456af46a8854774faeb294"
DEST = Path(__file__).resolve().parents[1] / "app/models/std.rnnn"
DEST.parent.mkdir(exist_ok=True)
if not DEST.exists() or hashlib.sha256(DEST.read_bytes()).hexdigest() != SHA:
    data = urllib.request.urlopen(f"https://raw.githubusercontent.com/richardpl/arnndn-models/{REV}/std.rnnn", timeout=60).read(1_000_000)
    if hashlib.sha256(data).hexdigest() != SHA:
        raise RuntimeError("RNNoise model checksum mismatch")
    DEST.write_bytes(data)
print("Verified original RNNoise model (302903 bytes)")
