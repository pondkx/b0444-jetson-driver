# Arducam B0444 IMX462 driver for Jetson Orin Nano

This is a kernel driver that makes the Arducam B0444 camera work on the
NVIDIA Jetson Orin Nano. I built it on JetPack 6.2.1 (L4T R36.4.7).

## What is the B0444

It is a small camera made by Arducam. It uses a Sony IMX462 sensor and
is good in low light. Arducam sells it for the Raspberry Pi and they
say it does not officially work on Jetson boards. That is what this
driver fixes.

## Why this was hard

The B0444 is not a normal camera. Between the Sony chip and the Jetson
there is a small computer (a chip called MCU) made by Arducam. The MCU
talks a different language than the bare Sony chip. So a normal IMX462
driver that talks Sony language gets nothing back from the camera.

I started from a great driver by Kurokesu that already works for a bare
IMX462 sensor. Then I changed three things so it talks to the Arducam
MCU instead of the bare sensor.

## What I changed

1. **The address on the I2C bus.**
   Bare IMX462 sits at address 0x1a. The Arducam MCU sits at 0x0c.
   I changed the device tree file to point at 0x0c.

2. **Skip the chip ID check.**
   The Kurokesu driver tries to read the Sony chip ID at power on. The
   Arducam MCU does not answer that question (it talks a different
   language). So I made the driver skip that check. Without this change
   the driver fails to load.

3. **Start and stop streaming the Arducam way.**
   The Kurokesu driver writes to Sony registers to start streaming. The
   Arducam MCU ignores those writes. The MCU expects you to write a `1`
   to register `0x0100` to start, and `0` to stop. I added a small
   helper function `pivariety_write_u32()` that does this, and I made
   `start_streaming` and `stop_streaming` use it.

That is it. The Kurokesu mode tables (the timing numbers for the
sensor) are correct as is, so I did not change those.

## How to install

You need a Jetson Orin Nano with JetPack 6.2.1 (or 6.2.2). Then:

```
sudo apt install -y --no-install-recommends dkms
git clone https://github.com/pondkx/b0444-jetson-driver.git
cd b0444-jetson-driver
sudo ./setup.sh
```

After install, reboot and check `lsmod | grep imx462`. You should see
`nv_imx462` loaded.

## How to check it works

After reboot, scan the camera I2C bus and look for the camera at 0x0c:

```
sudo i2cdetect -y -r 9
```

You should see `UU` at address `0c`. The `UU` means "device is here and
the kernel driver is using it".

Then look at the boot messages:

```
sudo dmesg | grep B0444
```

You should see:

```
imx462 9-000c: B0444 MCU-bridge mode: bypassing chip ID check
imx462 9-000c: B0444: sending Pivariety STREAM_ON (when capturing)
```

## How to capture a frame

```
sudo v4l2-ctl --device=/dev/video0 \
  --set-fmt-video=width=1920,height=1080,pixelformat=RG10 \
  --stream-mmap --stream-count=1 --stream-to=frame.raw
```

That gives you a 4.1 MB raw Bayer file. To see it as a picture, use
Kurokesu's `view_raw.py`:

```
python3 view_raw.py frame.raw
```

Or convert to PNG with the small script in `samples/`.


## Watch live video

The repo ships a Python live viewer:

```
sudo -u x bash -c "DISPLAY=:0 XAUTHORITY=/run/user/1000/gdm/Xauthority python3 imx462_live.py"
```

It opens fullscreen on the Jetson HDMI screen, shows a frame counter
and FPS, white-patch white balance, saturation boost, and a live Bayer
pattern cycler. Press `q` in the window to quit. Runs at about
**40 fps with ~25 ms latency** on the Orin Nano CPU. Three things make
it fast:

- A reader thread always grabs the freshest v4l2 frame and drops stale
  ones, so the main loop never processes a buffer that's older than
  one frame.
- White balance and percentile-stretch math live in a side thread that
  refreshes ~1.5 Hz, and the main loop drifts smoothly toward the new
  values 2% per frame so colours don't strobe.
- Debayer is a half-resolution numpy stride trick instead of
  `cv2.cvtColor`, then upscaled at the very end with `cv2.resize` to
  whatever size your HDMI monitor is actually running at (detected via
  `xrandr` at startup).

You can also drive the viewer over SSH (no keyboard needed on the
Jetson) by echoing letters into `/tmp/imx462_ctl`:

```
echo b > /tmp/imx462_ctl    # cycle Bayer pattern
echo w > /tmp/imx462_ctl    # toggle WB on/off
echo + > /tmp/imx462_ctl    # +0.5 saturation
echo - > /tmp/imx462_ctl    # -0.5 saturation
echo s > /tmp/imx462_ctl    # save snapshot to /tmp/imx462_snap.png
echo q > /tmp/imx462_ctl    # quit
```

If your HDMI is unreliable (mine is), use the UDP streaming scripts in
`streaming/` instead to watch the camera on another computer on the
same LAN. See `streaming/README.md` for the one-line setup.

### About the colours

The B0444 ships without an IR-cut filter — that is by design, Arducam
sells it as a low-light/NIR-sensitive module. The IMX462's organic
colour filters are nearly transparent to near-infrared, so under normal
indoor light every surface picks up some IR and the picture comes out
with a pink/magenta wash. Skin reads pinker than it is, plants and
fabric shift toward red/purple, white LEDs read greener than they look
to your eye. No software white balance can fully undo this because the
information is mixed in before the sensor sees it. The proper fix is a
$5 M12 UV/IR-cut filter screwed in between the lens and the sensor.

## What still needs work

- The MCU on the B0444 firmware does not seem to implement every V4L2
  control. WB writes go through but the visible effect is small. A
  nicer driver would walk `CTRL_INDEX_REG` at probe time and only
  register the controls the MCU answers for.
- `set_frame_rate` still writes Sony native VMAX registers that the
  MCU ignores (same bug the exposure/gain fix in v0.3.0 solved). Easy
  follow-up: route it through `pivariety_set_ctrl(V4L2_CID_FRAMERATE)`.
- There is no ISP tuning file for this sensor on Jetson, so
  `nvarguscamerasrc` does not work. Use the raw V4L2 path instead.
- Only `cam0` is wired up in the device tree right now.
- For real-time use without an IR-cut filter, the colours will be
  pinkish in visible light. A $5 M12 UV/IR-cut filter is the proper
  hardware fix; a CCM in the viewer is the software band-aid.
- The viewer hits ~40 fps on CPU. A CUDA debayer (NVIDIA NPP or
  RidgeRun's `rrcudadebayer`) would push that to 100+ fps but needs an
  OpenCV-CUDA rebuild on the Jetson.

## Sample picture

In `samples/first_frame_no_tuning.png` you can see a real picture from
the camera. It is dark and a bit green because there is no white
balance yet, but it proves the driver works end to end.

## Credit

The original driver is from Kurokesu (UAB), GPL-2.0. Their README is
saved as `KUROKESU_README.md` in this folder.

Original repo: https://github.com/Kurokesu/imx462-jetson-driver

Without their work this would have taken weeks of kernel hacking
instead of a few hours of patching.

## Changelog

- **v0.3.0** - Routed Tegracam's `set_exposure` / `set_gain` through
  `pivariety_set_ctrl()` so the v4l2 exposure / gain dials actually
  reach the MCU bridge instead of writing dead Sony registers. Rewrote
  the live viewer to use a reader thread (latest-frame-only),
  side-thread WB/p99 stats, half-resolution numpy debayer, gamma LUT,
  and `xrandr` screen-size detection: 3-5 fps + 600 ms lag became
  40 fps + 25 ms lag. Added a `/tmp/imx462_ctl` command channel so the
  viewer is fully SSH-drivable.
- **v0.2.0** - Added `wait_idle` + `read_u32` MCU helpers (fixes
  first-capture-after-boot failures). Added white balance V4L2
  controls (`red_balance`, `blue_balance`, `white_balance_automatic`)
  wired through the Pivariety CTRL_ID / CTRL_VALUE protocol. Added
  `imx462_live.py` live viewer and UDP streaming scripts in
  `streaming/`.
- **v0.1.0** - Initial fork of Kurokesu's IMX462 driver. Three patches
  so the same code works with the Arducam B0444 MCU bridge instead of
  a bare Sony sensor.

## License

GPL-2.0 (same as Kurokesu's original). See `LICENSE`.
