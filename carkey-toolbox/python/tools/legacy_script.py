"""Template for wrapping an existing script/CLI: runs a subprocess, streams each output
line to the UI and supports Stop. Replace DEMO with your real script (simulator, flash tool, ...)."""
import subprocess
import sys

from toolkit import tool, Param

# Example: a legacy script printing to stdout. Replace with ["python", "path/to/your_script.py", ...]
DEMO = "import time\nfor i in range(10):\n    print(f'step {i}', flush=True)\n    time.sleep(0.3)\n"


@tool(
    name="legacy_script",
    title="Run existing script (template)",
    description="Template: run an existing script/CLI as a subprocess and stream its output.",
    category="Examples",
    params=[Param("args", "string", label="Extra arguments", default="")],
)
def run(ctx, args):
    cmd = [sys.executable, "-u", "-c", DEMO] + args.split()
    ctx.log("$ " + " ".join(cmd[:3]) + " ...")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    lines = 0
    try:
        for line in proc.stdout:
            ctx.log(line.rstrip())
            lines += 1
            ctx.progress(lines / 10)
            if ctx.cancelled:
                proc.terminate()
                ctx.check_cancel()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"script exited with code {proc.returncode}")
    return {"returncode": proc.returncode, "lines": lines}
