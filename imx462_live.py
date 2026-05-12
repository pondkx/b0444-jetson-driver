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

t0, frame_count = time.time(), 0
while True:
    buf = proc.stdout.read(FRAME_BYTES)
    if len(buf) < FRAME_BYTES:
        print(f"short read {len(buf)} bytes, stopping", flush=True)
        break

    data = np.frombuffer(buf, dtype=np.uint16).reshape((H, W))
    raw10 = (data >> 6).astype(np.uint16)

    bgr = cv2.cvtColor(raw10, cv2.COLOR_BayerRG2BGR).astype(np.float32)

    # Aggressive auto-stretch so dim frames look bright.
    # Stretch the 99th percentile to white and apply gamma.
    p99 = np.percentile(bgr, 99.5)
    if p99 < 10: p99 = 10
    scaled = np.clip(bgr / p99, 0, 1) ** (1/2.2)
    disp = (scaled * 255).astype(np.uint8)

    frame_count += 1
    fps = frame_count / max(time.time() - t0, 0.001)
    cv2.putText(disp, f"Frame {frame_count}  {fps:.1f} fps  p99={p99:.0f}/1023",
                (30, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 3)
    cv2.putText(disp, "Press q in this window to quit",
                (30, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

    cv2.imshow(WIN, disp)
    if frame_count % 30 == 0:
        print(f"frame {frame_count}  {fps:.1f} fps  p99={p99:.0f}", flush=True)

    k = cv2.waitKey(1) & 0xFF
    if k == ord("q") or k == 27:
        break

proc.terminate()
cv2.destroyAllWindows()
print(f"Done. {frame_count} frames.", flush=True)
