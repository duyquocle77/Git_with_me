"""Mô phỏng phone tiến lại gần xe, ước lượng khoảng cách từ RSSI và phân vùng
(welcome / approach / unlock). Dùng để demo progress + cancel + kết quả dạng bảng."""
import math
import random

from toolkit import tool, Param


def rssi_at(distance_m, tx_power, n):
    # Log-distance path loss model
    return tx_power - 10 * n * math.log10(max(distance_m, 0.1))


def distance_from_rssi(rssi, tx_power, n):
    return 10 ** ((tx_power - rssi) / (10 * n))


def zone(d):
    if d <= 2.0:
        return "UNLOCK"
    if d <= 5.0:
        return "APPROACH"
    if d <= 10.0:
        return "WELCOME"
    return "FAR"


@tool(
    name="ble_rssi_sim",
    title="BLE RSSI approach simulator",
    description="Mô phỏng phone đi từ xa lại gần xe, lọc RSSI (EMA) và phân vùng khoảng cách.",
    category="BLE",
    params=[
        Param("start_m", "float", label="Khoảng cách bắt đầu (m)", default=15.0),
        Param("speed", "float", label="Tốc độ đi bộ (m/s)", default=1.2),
        Param("tx_power", "int", label="TX power @1m (dBm)", default=-59),
        Param("path_loss_n", "float", label="Path loss exponent", default=2.2),
        Param("noise_db", "float", label="Nhiễu (dB, std)", default=4.0),
        Param("alpha", "float", label="EMA alpha", default=0.3),
        Param("interval_ms", "int", label="Chu kỳ quảng bá (ms)", default=200),
    ],
)
def run(ctx, start_m, speed, tx_power, path_loss_n, noise_db, alpha, interval_ms):
    dt = interval_ms / 1000.0
    d = start_m
    filtered = None
    last_zone = None
    events = []
    t = 0.0
    while d > 0.5:
        ctx.check_cancel()
        raw = rssi_at(d, tx_power, path_loss_n) + random.gauss(0, noise_db)
        filtered = raw if filtered is None else alpha * raw + (1 - alpha) * filtered
        est = distance_from_rssi(filtered, tx_power, path_loss_n)
        z = zone(est)
        if z != last_zone:
            ctx.log(f"t={t:5.1f}s  real={d:5.2f}m  est={est:5.2f}m  rssi={filtered:6.1f}dBm  -> {z}",
                    "warn" if z == "UNLOCK" else "info")
            events.append({"t_s": round(t, 2), "real_m": round(d, 2), "est_m": round(est, 2), "zone": z})
            last_zone = z
        ctx.progress(1 - (d - 0.5) / (start_m - 0.5), f"{d:.1f} m")
        d -= speed * dt
        t += dt
        ctx.sleep(dt / 4)  # chạy nhanh gấp 4 lần thời gian thực
    return {"zone_transitions": events, "duration_s": round(t, 2)}
