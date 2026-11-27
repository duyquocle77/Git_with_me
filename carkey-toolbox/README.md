# Car Key Toolbox

Desktop app chạy local (Go + [Wails v2](https://wails.io)) để gom các tool / script / simulator
Python của team smart car access (phone key) vào một chỗ, có UI chung, log, progress, Stop, lưu kết quả.

## Kiến trúc

```
┌───────────── Wails app (1 file thực thi) ─────────────┐
│  Frontend (HTML/JS, WebView)                          │
│     │  window.go.main.App.RunTool(...)   ▲ events     │
│     ▼                                    │ run.log …  │
│  Go: app.go  ──►  internal/pybridge  ────┘            │
└───────────────────────┬───────────────────────────────┘
                        │ JSON-RPC 2.0, 1 dòng/message, qua stdin/stdout
                        ▼
            python/worker.py  (tiến trình con, sống suốt phiên)
                        │ import
                        ▼
            python/tools/*.py  ← tool của bạn (@tool decorator)
```

Vì sao chọn cách này:
- **Python giữ nguyên**: script/API Python có sẵn chỉ cần bọc bằng `@tool`, không viết lại sang Go.
- **stdin/stdout thay vì HTTP**: không mở port, không bị firewall công ty chặn, không xung đột khi nhiều người chạy.
- **Worker sống lâu**: import thư viện nặng (python-can, bleak, crypto...) một lần, chạy tool nhanh.
- **Cô lập lỗi**: tool lỗi chỉ báo đỏ trên UI; worker crash thì bấm *Restart Python*.
- **Kênh protocol an toàn**: `worker.py` tách fd stdout gốc riêng cho JSON-RPC, nên `print()` hay subprocess
  ghi stdout không làm hỏng giao tiếp. `print()` trong tool được hiển thị thành log.

## Cài đặt môi trường dev

| | Yêu cầu |
|---|---|
| Tất cả | Go ≥ 1.22, Python ≥ 3.10, `go install github.com/wailsapp/wails/v2/cmd/wails@v2.10.1` |
| Windows | WebView2 runtime (có sẵn trên Win 10/11) |
| macOS | Xcode command line tools |
| Ubuntu 24.04 | `sudo apt install libgtk-3-dev libwebkit2gtk-4.1-dev` (build thêm `-tags webkit2_41`) |

Kiểm tra bằng `wails doctor`.

```bash
cd carkey-toolbox
python -m venv python/.venv                    # app tự dùng venv này nếu có
python/.venv/bin/pip install -r python/requirements.txt   # Windows: python\.venv\Scripts\pip

wails dev                                      # chạy dev, sửa frontend tự reload
wails build                                    # ra build/bin/carkey-toolbox(.exe)
go test ./internal/...                         # test bridge Go <-> Python
```

## Thêm một tool mới

Tạo file `python/tools/<ten_tool>.py` (copy từ `hello.py`):

```python
from toolkit import tool, Param

@tool(
    name="uwb_ranging_log",
    title="UWB ranging log analyzer",
    description="Đọc log UWB và thống kê khoảng cách",
    category="UWB",
    params=[
        Param("log_file", "file", label="File log", required=True),
        Param("max_m", "float", label="Ngưỡng (m)", default=3.0),
    ],
)
def run(ctx, log_file, max_m):
    ctx.log("Bắt đầu...")          # hoặc print(...)
    for i, line in enumerate(open(log_file)):
        ctx.check_cancel()          # cho phép nút Stop hoạt động
        ...
        ctx.progress(i / total)     # 0..1
    return {"count": ...}           # dict/list JSON được -> hiển thị + lưu file
```

Bấm **⟳ Reload tools** trên app — không cần build lại.

Kiểu tham số: `string`, `int`, `float`, `bool`, `choice` (kèm `options=[...]`), `file` (có nút chọn file).

Script/CLI có sẵn không muốn sửa: xem `tools/legacy_script.py` (chạy subprocess, stream output, hỗ trợ Stop).

Tool đặt ngoài repo (VD thư mục share của team): khai báo `CARKEY_TOOLS_PATH=/path/a:/path/b`
(Windows dùng `;`).

## Biến môi trường

| Biến | Ý nghĩa |
|---|---|
| `CARKEY_PYTHON` | Đường dẫn interpreter cụ thể (mặc định: `python/.venv`, rồi `python3`/`python`/`py` trên PATH) |
| `CARKEY_PYTHON_DIR` | Thư mục chứa `worker.py` (mặc định: cạnh file exe, hoặc `./python`) |
| `CARKEY_TOOLS_PATH` | Thêm thư mục tool, phân tách bằng `:` (Windows `;`) |

## Chia sẻ cho đồng nghiệp

Giai đoạn 1 (đơn giản, đủ dùng nội bộ): gửi một thư mục gồm
```
carkey-toolbox(.exe)
python/            (worker.py, toolkit.py, tools/, requirements.txt)
```
Đồng nghiệp cần cài Python + `pip install -r python/requirements.txt` (hoặc tạo `python/.venv`).

Giai đoạn sau (xem roadmap): đóng gói luôn Python (python-build-standalone hoặc embeddable zip trên Windows)
để người dùng không cần cài gì.

## Protocol (tham khảo)

Go → Python: `ping`, `list_tools`, `reload_tools`, `run_tool {run_id, tool, params}`, `cancel {run_id}`.
Python → Go (notification): `run.log`, `run.progress`, `run.finished`, `worker.log`.
Go chuyển nguyên các notification này thành Wails events cho frontend (thêm `run.started`, `worker.status`).

## Roadmap gợi ý

- [ ] Gom tool thật của team vào `python/tools/` (mỗi nhóm một category: BLE, UWB, NFC/SE, CAN, Diag...)
- [ ] Đóng gói Python embedded + `requirements.txt` vào bản build để share 1-click
- [ ] Tool chạy lâu dạng "session" (giữ kết nối serial/CAN/BLE mở, gửi lệnh qua lại) + biểu đồ realtime
- [ ] Profile cấu hình (VIN, key ID, cổng COM...) dùng chung giữa các tool
- [ ] Repo tool dùng chung của team + kiểm tra phiên bản / auto-update
- [ ] CI build Windows/macOS/Linux bằng GitHub Actions
