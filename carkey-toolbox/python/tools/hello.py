"""Template nhỏ nhất: copy file này để bắt đầu một tool mới."""
from toolkit import tool, Param


@tool(
    name="hello",
    title="Hello (template)",
    description="Tool mẫu tối giản. Copy file này để tạo tool mới.",
    category="Examples",
    params=[
        Param("who", "string", label="Tên", default="team"),
        Param("times", "int", label="Số lần", default=3),
    ],
)
def hello(ctx, who, times):
    for i in range(times):
        ctx.check_cancel()
        print(f"[{i + 1}/{times}] Xin chào {who}!")  # print() hiển thị như log trên UI
        ctx.progress((i + 1) / times)
        ctx.sleep(0.3)
    return {"greeting": f"Xin chào {who}", "times": times}
