"""Simulates a CCS headless build so the CI/CD tab can be tried without CCS installed."""
import argparse
import pathlib
import random
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("--project", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--fail", default="false")
a = ap.parse_args()

out = pathlib.Path(a.out)
out.mkdir(parents=True, exist_ok=True)
print(f"**** Build of configuration Release for project {a.project} ****", flush=True)
for src in ["main.c", "ble_stack.c", "uwb_ranging.c", "ccc_digital_key.c", "nvm.c", "startup.c"]:
    print(f'Building file: "../src/{src}"', flush=True)
    print(f'Invoking: Arm Compiler  "tiarmclang" -mcpu=cortex-m33 -O2 ... "../src/{src}"', flush=True)
    time.sleep(0.15)
    if a.fail.lower() == "true" and src == "uwb_ranging.c":
        print(f'../src/{src}:42:5: error: use of undeclared identifier "ranging_cfg"', flush=True)
        print("gmake: *** [src/uwb_ranging.o] Error 1", flush=True)
        print("**** Build Failed ****", flush=True)
        sys.exit(2)
print(f'Building target: "{a.project}.out"', flush=True)
for ext, data in [(".out", b"\x7fELF fake"), (".map", b"MEMORY CONFIGURATION\n"), (".hex", b":020000040000FA\n:00000001FF\n")]:
    (out / f"{a.project}{ext}").write_bytes(data)
print(f"Finished building target: \"{a.project}.out\"  ({random.randint(80, 200)} KB flash)", flush=True)
print("**** Build Finished ****", flush=True)
