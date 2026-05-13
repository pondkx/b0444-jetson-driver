#!/usr/bin/env python3
# Live viewer for B0444 IMX462 via raw V4L2.
# Pure software path: v4l2-ctl pipe -> numpy -> cv2 debayer -> cv2.imshow.
# No nvarguscamerasrc, no EGL, no NVMM. Safe for the Jetson compositor.

import cv2, numpy as np, subprocess, sys, time

W, H = 1920, 1080
FRAME_BYTES = W * H * 2
WIN = "B0444 LIVE"

print("Starting v4l2-ctl raw stream...", flush=True)
cmd = ["sudo", "v4l2-ctl", "--device=/dev/video0",
       "--set-fmt-video=width=1920,height=1080,pixelformat=RG10",
       "--stream-mmap", "--stream-count=0", "--stream-to=-"]
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
cv2.setWindowProperty(WIN, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
print("Window opened, reading frames...", flush=True)

# Bayer pattern cycle. Press 'b' to step through.
BAYER_LIST = [
    ("BGGR", cv2.COLOR_BayerBG2BGR),
    ("RGGB", cv2.COLOR_BayerRG2BGR),
    ("GRBG", cv2.COLOR_BayerGR2BGR),
    ("GBRG", cv2.COLOR_BayerGB2BGR),
]
bayer_idx = 0

# Simple gray-world WB. Toggle with 'w'.
wb_on = True

# Saturation boost. Press '+' to increase, '-' to decrease.
sat_boost = 2.0

t0, frame_count = time.time(), 0
while True:
    buf = proc.stdout.read(FRAME_BYTES)
    if len(buf) < FRAME_BYTES:
        print(f"short read {len(buf)} bytes, stopping", flush=True)
        break

    data = np.frombuffer(buf, dtype=np.uint16).reshape((H, W))
    raw10 = (data >> 6).astype(np.uint16)

    name, code = BAYER_LIST[bayer_idx]
    bgr = cv2.cvtColor(raw10, code).astype(np.float32)

    if wb_on:
        # White-patch: find the brightest pixels (top 0.5%) and force them
        # to white. Works well when there's a real white reference in the
        # scene (LED, ceiling light, paper).
        per_ch_top = np.percentile(bgr.reshape(-1, 3), 99.5, axis=0)
        ref = per_ch_top.max()
        scale = ref / np.maximum(per_ch_top, 1.0)
        bgr = bgr * scale

    p99 = np.percentile(bgr, 99.5)
    if p99 < 10: p99 = 10
    scaled = np.clip(bgr / p99, 0, 1) ** (1/2.2)
    disp = (scaled * 255).astype(np.uint8)

    if sat_boost != 1.0:
        hsv = cv2.cvtColor(disp, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[..., 1] = np.clip(hsv[..., 1] * sat_boost, 0, 255)
        disp = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

    frame_count += 1
    fps = frame_count / max(time.time() - t0, 0.001)
    cv2.putText(disp,
        f"Frame {frame_count}  {fps:.1f} fps  bayer={name}  wb={'on' if wb_on else 'off'}  sat={sat_boost:.1f}  p99={p99:.0f}",
        (30, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 3)
    cv2.putText(disp, "b=bayer  w=wb  +/-=sat  s=save  q=quit",
                (30, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

    cv2.imshow(WIN, disp)
    if frame_count % 30 == 0:
        print(f"frame {frame_count}  {fps:.1f} fps  bayer={name}  wb={wb_on}  p99={p99:.0f}", flush=True)

    k = cv2.waitKey(1) & 0xFF
    cmd_letter = None
    try:
        with open("/tmp/imx462_ctl", "r") as f:
            cmd_letter = f.read().strip()[:1]
        open("/tmp/imx462_ctl", "w").close()
    except FileNotFoundError:
        pass

    if k == ord("q") or k == 27 or cmd_letter == "q":
        break
    elif k == ord("b") or cmd_letter == "b":
        bayer_idx = (bayer_idx + 1) % len(BAYER_LIST)
        print(f"switched to bayer {BAYER_LIST[bayer_idx][0]}", flush=True)
    elif k == ord("w") or cmd_letter == "w":
        wb_on = not wb_on
        print(f"wb {'on' if wb_on else 'off'}", flush=True)
    elif cmd_letter == "s":
        cv2.imwrite("/tmp/imx462_snap.png", disp)
        print("saved /tmp/imx462_snap.png", flush=True)
    elif k == ord("+") or k == ord("=") or cmd_letter == "+":
        sat_boost = min(sat_boost + 0.5, 6.0)
        print(f"sat={sat_boost:.1f}", flush=True)
    elif k == ord("-") or cmd_letter == "-":
        sat_boost = max(sat_boost - 0.5, 0.0)
        print(f"sat={sat_boost:.1f}", flush=True)

proc.terminate()
cv2.destroyAllWindows()
print(f"Done. {frame_count} frames.", flush=True)
