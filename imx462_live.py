#!/usr/bin/env python3
# Live viewer for the B0444 IMX462 on the Jetson.
#
# Why this exists at all: nvarguscamerasrc + nvbufsurftransform
# completely wedged the Jetson compositor every time we tried it. EGL
# would die, the display would freeze, USB would drop, the whole desk
# would need a reboot. So we ditched anything NVIDIA-ISP and built a
# pure software path: v4l2-ctl pipes raw 10-bit Bayer over stdin, numpy
# debayers it, OpenCV shows it. No GPU, no DRM, no compositor stress.
#
# How we got here in evening order:
#   1. first cut just did cv2.cvtColor + WB + percentile every frame.
#      It worked but ran at 3-5 fps and lagged the user by ~0.6 sec.
#   2. moved the AWB and percentile math into a side thread, dropped
#      the cv2 debayer for a hand-written half-res one in numpy, and
#      added a reader thread that always grabs the freshest frame.
#      Jumped to 60 fps with ~17 ms latency.
#   3. discovered the screen was 2560x1440 not 1920x1080 so the image
#      was sitting in the top-left of the HDMI screen. Added xrandr
#      detection. Now fills the whole monitor.
#   4. the white-patch WB was flickering colors like a strobe because
#      the brightest pixel kept hopping between objects. Added a slow
#      per-frame EMA so it drifts smoothly instead of jumping.
#
# The image still looks pink-ish overall. That is NOT a bug in this
# script — it is the IMX462 with no IR-cut filter, NIR light leaking
# into all three colour channels. Software can't fix it; a $5 M12 IR
# filter would.

import cv2
import numpy as np
import os
import re
import subprocess
import threading
import time

# Sensor delivers 1920x1080 RAW10 packed as 16-bit (one frame = 4 MB).
W, H = 1920, 1080
FRAME_BYTES = W * H * 2

# We process at half res to keep up at 30+ fps. Upscale just before
# imshow so the user still gets a full-screen picture.
DISP_W, DISP_H = W // 2, H // 2  # 960x540

WIN = "B0444 LIVE"


def detect_screen_size(default=(1920, 1080)):
    """Ask xrandr what the HDMI monitor is actually running at, so the
    cv2 fullscreen window can stretch the image to that size instead of
    drawing it 1:1 in the corner. Falls back to 1080p if xrandr fails."""
    try:
        out = subprocess.check_output(
            ["xrandr"],
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
            timeout=2).decode()
        m = re.search(r"current\s+(\d+)\s*x\s*(\d+)", out)
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return default


# Bayer patterns. The names below are OpenCV's labels (which are
# inverted from how camera datasheets name them — OpenCV's "BayerBG"
# actually puts Red at the top-left of the 2x2 block, not Blue). We
# keep OpenCV's naming so people can compare with cv2.COLOR_BayerXX2BGR.
# Each entry says "where is the R pixel" then "where is the B pixel"
# in the 2x2 block.
BAYER_PATTERNS = {
    "BGGR": ((0, 0), (1, 1)),  # = cv2.COLOR_BayerBG2BGR -> R top-left
    "RGGB": ((1, 1), (0, 0)),  # = cv2.COLOR_BayerRG2BGR -> B top-left
    "GRBG": ((1, 0), (0, 1)),  # = cv2.COLOR_BayerGR2BGR
    "GBRG": ((0, 1), (1, 0)),  # = cv2.COLOR_BayerGB2BGR
}
BAYER_NAMES = list(BAYER_PATTERNS.keys())

# Gamma curve baked into a 256-entry lookup table so each frame only
# pays for an indexed memory read instead of a real pow() per pixel.
GAMMA = 1.0 / 2.2
GAMMA_LUT = np.array([((i / 255.0) ** GAMMA) * 255 for i in range(256)],
                     dtype=np.uint8)


def half_res_debayer(raw10, pattern):
    """Turn a 1920x1080 Bayer plane into a 960x540 BGR uint16 image
    without calling cv2.cvtColor. Each 2x2 block becomes one output
    pixel: we pick the R sample, average the two G samples, pick the B
    sample. This is "downsampling demosaic" — way faster than a real
    interpolation, and it's fine because we're feeding the screen, not
    a vision model."""
    (rr, rc), (br, bc) = pattern
    R = raw10[rr::2, rc::2]
    B = raw10[br::2, bc::2]

    # The two G positions in a Bayer block are always the OTHER diagonal
    # from R/B. Two cases depending on which diagonal R sits on.
    if (rr + rc) % 2 == 0:
        # R is on the even diagonal -> G samples are on the odd one.
        G = (raw10[0::2, 1::2].astype(np.uint32) +
             raw10[1::2, 0::2].astype(np.uint32)) >> 1
    else:
        # R is on the odd diagonal -> G samples are on the even one.
        G = (raw10[0::2, 0::2].astype(np.uint32) +
             raw10[1::2, 1::2].astype(np.uint32)) >> 1

    out = np.empty((R.shape[0], R.shape[1], 3), dtype=np.uint16)
    out[..., 0] = B
    out[..., 1] = G.astype(np.uint16)
    out[..., 2] = R
    return out


def reader_loop(proc, slot, stop_evt):
    """Single job: keep slot['buf'] pointed at the newest frame from
    v4l2-ctl, and bump slot['seq'] every time it changes. The main loop
    only ever processes whatever is sitting in slot at the moment it
    looks, so older frames get silently dropped. That is exactly what
    killed the ~600 ms lag the user could see on screen."""
    while not stop_evt.is_set():
        buf = proc.stdout.read(FRAME_BYTES)
        if len(buf) < FRAME_BYTES:
            # v4l2-ctl closed the pipe or gave us a short frame; bail
            # cleanly and let the main loop exit.
            stop_evt.set()
            return
        with slot["lock"]:
            slot["buf"] = buf
            slot["seq"] += 1


def stats_loop(state, stop_evt):
    """Recompute the WB scale + p99 stretch TARGET every ~0.6 sec.
    We never apply this directly — the main loop slides toward it at
    ~2% per frame. That converts a 1-Hz staircase (which the user said
    looked like 'moving down stairs') into a smooth drift."""
    UPDATE_PERIOD = 0.6
    while not stop_evt.is_set():
        time.sleep(UPDATE_PERIOD)
        with state["lock"]:
            bgr = state.get("bgr_for_stats")
        if bgr is None:
            continue

        # White-patch WB: pick the 99.5th-percentile value in each
        # channel and scale all three so the brightest patch reads white.
        per_ch = np.percentile(bgr.reshape(-1, 3), 99.5, axis=0)
        ref = float(per_ch.max())
        wb_target = (ref / np.maximum(per_ch, 1.0)).astype(np.float32)

        # After WB, find where the new "99.5th percentile pixel" lands.
        # That's our exposure stretch target so the output uses the
        # whole 0..255 range without blowing highlights.
        p99_target = float(np.percentile(bgr * wb_target, 99.5))
        if p99_target < 10:
            p99_target = 10

        with state["lock"]:
            state["wb_target"] = wb_target
            state["p99_target"] = p99_target


def main():
    print("Starting v4l2-ctl raw stream...", flush=True)
    # We use the v4l2-ctl pipe instead of cv2.VideoCapture because
    # OpenCV's V4L backend can't speak RG10 (10-bit packed Bayer).
    cmd = ["sudo", "v4l2-ctl", "--device=/dev/video0",
           "--set-fmt-video=width=1920,height=1080,pixelformat=RG10",
           "--stream-mmap", "--stream-count=0", "--stream-to=-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)

    screen_w, screen_h = detect_screen_size()
    print(f"Detected screen size: {screen_w}x{screen_h}", flush=True)

    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(WIN, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    # FREERATIO lets the image stretch to fill the window, otherwise
    # cv2 draws it 1:1 in the top-left corner of the fullscreen window.
    cv2.setWindowProperty(WIN, cv2.WND_PROP_ASPECT_RATIO, cv2.WINDOW_FREERATIO)
    print("Window opened.", flush=True)

    # 'slot' is where the reader thread parks the freshest frame.
    # 'state' carries the WB/p99 numbers between the stats thread and main.
    slot = {"buf": None, "seq": 0, "lock": threading.Lock()}
    state = {
        "lock": threading.Lock(),
        "bgr_for_stats": None,
        "wb_scale": np.array([1.0, 1.0, 1.0], dtype=np.float32),
        "wb_target": np.array([1.0, 1.0, 1.0], dtype=np.float32),
        "p99": 512.0,
        "p99_target": 512.0,
    }
    stop_evt = threading.Event()

    threading.Thread(target=reader_loop, args=(proc, slot, stop_evt),
                     daemon=True).start()
    threading.Thread(target=stats_loop, args=(state, stop_evt),
                     daemon=True).start()

    bayer_idx = 0
    wb_on = True
    sat_boost = 2.0       # the user said colours look dull, so default >1
    last_seq = 0
    frame_count = 0
    t0 = time.time()
    fps_disp = 0.0

    # The amount we drift toward the new WB target every frame. With
    # ~40 fps this reaches 63% of a new target in roughly 1.2 sec, fast
    # enough to track real lighting changes, slow enough to never strobe.
    PER_FRAME_ALPHA = 0.02

    try:
        while not stop_evt.is_set():
            with slot["lock"]:
                seq = slot["seq"]
                buf = slot["buf"]
            if seq == last_seq or buf is None:
                # Nothing new yet — let cv2 pump events and try again.
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
                continue
            last_seq = seq

            # The sensor sends 10-bit data packed in the upper bits of a
            # 16-bit word. Right-shift by 6 to put it in 0..1023.
            data16 = np.frombuffer(buf, dtype=np.uint16).reshape((H, W))
            raw10 = data16 >> 6

            name = BAYER_NAMES[bayer_idx]
            bgr10 = half_res_debayer(raw10, BAYER_PATTERNS[name])

            # Hand a frame to the stats thread every 8 frames (cheap),
            # then slide our applied WB/p99 a tiny step toward the latest
            # target the stats thread published. This is the smoothing.
            with state["lock"]:
                if frame_count % 8 == 0:
                    state["bgr_for_stats"] = bgr10
                state["wb_scale"] = ((1 - PER_FRAME_ALPHA) * state["wb_scale"]
                                     + PER_FRAME_ALPHA * state["wb_target"])
                state["p99"] = ((1 - PER_FRAME_ALPHA) * state["p99"]
                                + PER_FRAME_ALPHA * state["p99_target"])
                wb = state["wb_scale"]
                p99 = state["p99"]

            # Combine WB + stretch into one per-channel scale factor.
            # cv2.convertScaleAbs is the cheapest "uint16 * float -> uint8
            # with clipping" you can do on the Jetson CPU.
            base = 255.0 / p99
            gains = wb * base if wb_on else np.array([base] * 3, dtype=np.float32)

            b8 = cv2.convertScaleAbs(bgr10[..., 0], alpha=float(gains[0]))
            g8 = cv2.convertScaleAbs(bgr10[..., 1], alpha=float(gains[1]))
            r8 = cv2.convertScaleAbs(bgr10[..., 2], alpha=float(gains[2]))
            disp = cv2.merge((b8, g8, r8))

            # Gamma in one shot through the LUT.
            disp = cv2.LUT(disp, GAMMA_LUT)

            # Saturation boost the cheap way: blend the image with its
            # own grayscale version. If sat=1.0 we'd just get the same
            # image back, so skip the work in that case.
            if sat_boost != 1.0:
                gray = cv2.cvtColor(disp, cv2.COLOR_BGR2GRAY)
                gray3 = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
                disp = cv2.addWeighted(disp, sat_boost, gray3,
                                       1 - sat_boost, 0)

            frame_count += 1
            if frame_count % 10 == 0:
                fps_disp = 10.0 / max(time.time() - t0, 0.001)
                t0 = time.time()

            cv2.putText(disp,
                f"f={frame_count} {fps_disp:.1f}fps b={name} "
                f"wb={'on' if wb_on else 'off'} sat={sat_boost:.1f} "
                f"p99={p99:.0f}",
                (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
            cv2.putText(disp, "b=bayer  w=wb  +/-=sat  s=save  q=quit",
                        (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (0, 255, 255), 1)

            # Final upscale to whatever the HDMI monitor actually is.
            show = cv2.resize(disp, (screen_w, screen_h),
                              interpolation=cv2.INTER_LINEAR)
            cv2.imshow(WIN, show)

            if frame_count % 30 == 0:
                print(f"frame {frame_count}  {fps_disp:.1f} fps  "
                      f"bayer={name}  p99={p99:.0f}", flush=True)

            # Drive the viewer either from the local keyboard OR from
            # commands echoed into /tmp/imx462_ctl (so we can poke it
            # over SSH without a keyboard attached to the Jetson).
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
                bayer_idx = (bayer_idx + 1) % len(BAYER_NAMES)
                print(f"switched to bayer {BAYER_NAMES[bayer_idx]}",
                      flush=True)
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
    finally:
        stop_evt.set()
        proc.terminate()
        cv2.destroyAllWindows()
        print(f"Done. {frame_count} frames.", flush=True)


if __name__ == "__main__":
    main()
