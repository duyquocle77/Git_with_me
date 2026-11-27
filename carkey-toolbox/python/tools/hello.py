"""Smallest possible tool: copy this file to start a new one."""
from toolkit import tool, Param


@tool(
    name="hello",
    title="Hello (template)",
    description="Minimal example tool. Copy this file to create a new tool.",
    category="Examples",
    params=[
        Param("who", "string", label="Name", default="team"),
        Param("times", "int", label="Times", default=3),
    ],
)
def hello(ctx, who, times):
    for i in range(times):
        ctx.check_cancel()
        print(f"[{i + 1}/{times}] Hello {who}!")  # print() shows up as a log line in the UI
        ctx.progress((i + 1) / times)
        ctx.sleep(0.3)
    return {"greeting": f"Hello {who}", "times": times}
