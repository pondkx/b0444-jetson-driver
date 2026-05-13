# The 3 patches I made to Kurokesu's driver

This is a quick view of what is different from the upstream Kurokesu
driver, in case anyone wants to make a similar patch for another
Arducam Pivariety camera (B0444 family).

## Patch 1: change I2C address in the device tree

File: `tegra234-p3767-camera-p3768-imx462-A.dts`

Change:
```
reg = <0x1a>;   ->   reg = <0x0c>;
```

Why: bare Sony IMX462 is at 0x1a. The Arducam MCU bridge on B0444 sits
at 0x0c.

## Patch 2: skip the Sony chip ID check

File: `nv_imx462.c`, function `imx462_board_setup()`

Before:
- The function powers the sensor on
- Reads the model ID register on the bare Sony chip
- Logs an error if the ID does not match 0x10A0
- Powers the sensor off

After:
- Powers on
- Prints a message saying "B0444 MCU-bridge mode: bypassing chip ID check"
- Powers off

Why: the Arducam MCU at 0x0c does not respond to Sony chip register
reads. The model ID register is on the bare Sony chip behind the MCU.
If we let the original code run, the I2C read fails with -121 (no
device) and the whole driver fails to probe.

## Patch 3: send Pivariety STREAM_ON to start streaming

File: `nv_imx462.c`

What I added:
- A new helper function `pivariety_write_u32()` that writes a 6-byte
  message to the I2C bus: 2 bytes for the register address (big
  endian) and 4 bytes for the value (big endian)
- Replaced the body of `imx462_start_streaming()` with a single call
  that writes `1` to MCU register `0x0100`
- Replaced the body of `imx462_stop_streaming()` similarly with a
  write of `0`

Why: the upstream driver writes to Sony registers like `0x3000`
(IMX462_STANDBY). Those writes go to the MCU at 0x0c which interprets
them differently and never tells the Sony chip to start. The MCU's own
protocol uses a register at 0x0100 to start and stop the sensor.

## Things I left alone

- Kurokesu's IMX462 mode table (`imx462_mode_tbls.h`) is fine. The
  timing numbers (active_w=1920, active_h=1080, line_length=4400,
  pix_clk_hz=148500000) match what the MCU actually sends out.
- Kurokesu's Tegra Camera Framework setup is fine.
- The DKMS build files are unchanged.
- The ISP tuning file (`tuning/camera_overrides.isp`) is unchanged
  (but not used by the raw V4L2 path the user takes).

## Why these patches are enough

The MCU bridge already configures the IMX462 internally. We do not
need to write any Sony register from the host side. We only need to
tell the MCU to "start streaming". Tegra's VI receives the MIPI CSI-2
data at the IMX462 native timing, which matches Kurokesu's mode table
(because Kurokesu wrote it for the same sensor).

So the minimal change is: change one address, skip one check, replace
one write. Three patches, less than 50 lines of code.


# More patches (added in v0.2.0)

## Patch 4: pivariety_read_u32 and pivariety_wait_idle

After a clean boot, the very first capture sometimes failed with a
flurry of VI errors. After playing a bit it always worked. The reason:
the Arducam MCU is busy for a few milliseconds after each I2C write
and back-to-back writes were stepping on each other.

Fix: read the SYSTEM_IDLE register (0x0107) and poll until the MCU
reports ready. I added two helpers:

- `pivariety_read_u32()` reads a 32-bit value from any MCU register
  using the standard I2C write-then-read pattern.
- `pivariety_wait_idle()` polls SYSTEM_IDLE_REG up to 20 times with
  1-2 ms sleeps. Returns when the MCU is ready.

Then `start_streaming` and `stop_streaming` call `wait_idle` around
their STREAM_ON writes. The first-capture-after-boot problem went
away.

## Patch 5: White balance V4L2 controls

The kernel driver now exposes three more V4L2 controls so userspace
can tune white balance:

- `V4L2_CID_RED_BALANCE` (`red_balance`, 0..4000, default 1500)
- `V4L2_CID_BLUE_BALANCE` (`blue_balance`, 0..4000, default 1500)
- `V4L2_CID_AUTO_WHITE_BALANCE` (`white_balance_automatic`, 0 or 1)

How it works: I added a separate v4l2_ctrl_handler in the imx462
struct, registered the three controls on it, then chained that
handler onto the Tegracam-owned subdev handler. Whenever userspace
writes one of these CIDs, the `imx462_wb_s_ctrl` callback fires and
forwards the value to the Arducam MCU using the new
`pivariety_set_ctrl()` helper.

`pivariety_set_ctrl()` follows the Pivariety protocol from the
Arducam open-source driver: write the V4L2 CID to `CTRL_ID_REG`
(0x0401), wait for idle, write the value to `CTRL_VALUE_REG`
(0x0406), wait for idle.

Important: the controls show up on `/dev/v4l-subdev1` (not on
`/dev/video0` because Tegracam does not chain custom handlers to
the bridge node). Use them like:

```
sudo v4l2-ctl --device=/dev/v4l-subdev1 -c red_balance=1550
sudo v4l2-ctl --device=/dev/v4l-subdev1 -c blue_balance=1650
```

Whether the MCU on a specific B0444 firmware actually implements
WB gain control is firmware-dependent. On mine the writes go
through without errors but visible effect is small. Future work:
enumerate the controls the MCU answers for by reading
CTRL_INDEX_REG, like the Pi driver does.

## Patch 6: live viewer + UDP streaming scripts

Not a kernel change, but a set of tools that make the driver actually
usable in practice:

- `imx462_live.py` — a small Python script that opens `/dev/video0`
  through a `v4l2-ctl --stream-to=-` subprocess pipe, debayers each
  frame on the CPU, applies an aggressive auto-stretch + sRGB gamma
  so dim sensor output is visible on screen, and shows the result
  fullscreen with `cv2.imshow`. Runs at ~4 fps at 1080p on the Orin
  Nano CPU. No GPU / NVMM / EGL touched, so it never wedges the
  display compositor.
- `streaming/jetson_send_h264_udp.sh` — Jetson side, a GStreamer
  `v4l2src ! bayer2rgb ! x264enc ! udpsink` pipeline that streams
  H.264 to another machine on the LAN.
- `streaming/mac_receive_gst.sh` and `streaming/b0444.sdp` — Mac
  side, either a GStreamer one-liner or VLC openable SDP file for
  the live receive.

Together these let you tune the camera controls from one terminal
while watching the output from another room without ever risking
the Jetson HDMI compositor.


# More patches (added in v0.3.0)

## Patch 7: route Tegracam set_exposure / set_gain through the MCU

The Tegracam framework calls a callback when userspace sets `exposure`
or `gain`. The callback Kurokesu shipped writes Sony native registers
(SHS1 at 0x3020 for exposure, GAIN at 0x3014 for analog gain). On the
B0444 those writes go to the MCU bridge which does not implement Sony's
register set — the writes are silently swallowed, and the sensor never
actually changes its gain or exposure.

We discovered this when the picture stayed dim in a bright room even
after maxing the `gain` and `exposure` v4l2 controls. Confirmation was
easy: `v4l2-ctl --set-ctrl=gain=295` did nothing visible on screen, but
the same value written via the Pivariety `CTRL_ID_REG / CTRL_VALUE_REG`
sequence (the same pattern we already used for white balance) made the
image visibly brighter.

Fix: replaced the bodies of `imx462_set_gain()` and
`imx462_set_exposure()` so they call

```
pivariety_set_ctrl(priv->i2c_client, V4L2_CID_ANALOGUE_GAIN, val);
pivariety_set_ctrl(priv->i2c_client, V4L2_CID_EXPOSURE,        val);
```

instead of writing dead Sony registers. The original Sony register
helpers (`imx462_get_gain_reg`, `imx462_set_coarse_time`, etc.) are
still in the file but no longer called — left there in case some
future firmware build of the MCU exposes a raw register-write path.

After this patch, `gain` and `exposure` actually change what the sensor
does. Range is firmware-determined by the MCU (typically 0..295 and
30..33275 line periods on this firmware).

## Patch 8: live viewer rewrite for low latency

The original `imx462_live.py` from v0.2.0 ran at 3-5 fps with about
600 ms of lag, which the user described as "I see myself in the past
when I wave my hand". Three independent problems:

1. The `v4l2-ctl --stream-mmap` pipe was queuing ~4 buffers (130 ms)
   before our Python loop pulled one.
2. Each frame was running two `np.percentile` calls (white balance +
   exposure stretch), each one sorting a 6-million-float array
   (~150-250 ms each).
3. `cv2.cvtColor(BayerXX2BGR)` on a full 1920x1080 image was ~50-80 ms
   single-threaded on the Orin Nano CPU.

Rewrote it to:

- Run a **reader thread** that always overwrites a single "latest
  frame" slot, so the main loop never processes a buffer older than
  ~1 frame. Drops the 130 ms queue latency.
- Run a **stats thread** that recomputes the WB scale and percentile
  stretch every ~0.6 sec, then the main loop EMA-slides toward that
  target 2% per frame. Recomputing only twice a second instead of every
  frame is the single biggest CPU win, and the EMA fixes the original
  flicker (when the brightest pixel in the frame moves to a different
  object the WB used to snap and the whole picture pulsed magenta).
- Replace `cv2.cvtColor` with a **half-resolution numpy stride
  debayer** (pick R from R positions, B from B positions, average the
  two G positions). Produces a 960x540 BGR uint16 image in ~5 ms
  instead of ~60 ms.
- Apply WB + stretch + gamma in one `cv2.convertScaleAbs` + `cv2.LUT`
  pass per channel — all uint8, no float32 allocations.
- Detect the actual HDMI resolution at startup with `xrandr` (the
  user's monitor is 2560x1440, not 1920x1080 as we assumed) and
  `cv2.resize` the 960x540 buffer up to fill the whole screen.
- Add a `/tmp/imx462_ctl` named-file command channel so the viewer
  can be driven over SSH without a keyboard attached to the Jetson
  HDMI. Letters: `b` cycle Bayer, `w` toggle WB, `+`/`-` saturation,
  `s` save snapshot, `q` quit.

End result: ~40 fps with ~25 ms latency on the Orin Nano CPU, no GPU,
no NVMM, no compositor risk.
