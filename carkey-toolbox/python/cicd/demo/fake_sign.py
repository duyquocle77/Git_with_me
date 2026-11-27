"""Simulates the OEM create & sign tool: combines APP + FBL hex into a signed bin."""
import argparse
import hashlib
import pathlib
import time

ap = argparse.ArgumentParser()
ap.add_argument("--app", required=True)
ap.add_argument("--fbl", required=True)
ap.add_argument("--out", required=True)
a = ap.parse_args()

data = pathlib.Path(a.fbl).read_bytes() + pathlib.Path(a.app).read_bytes()
out = pathlib.Path(a.out)
out.mkdir(parents=True, exist_ok=True)
print("Creating image ...", flush=True)
time.sleep(0.3)
print("Signing with HSM key slot 3 ...", flush=True)
time.sleep(0.3)
digest = hashlib.sha256(data).hexdigest()
(out / "image_signed.bin").write_bytes(data + bytes.fromhex(digest))
print(f"SHA-256: {digest}", flush=True)
print("Signed image written.", flush=True)
