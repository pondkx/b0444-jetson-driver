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

## What still needs work

The current driver only exposes these v4l2 controls: `gain`,
`exposure`, `frame_rate`, `horizontal_flip`, `vertical_flip`. To get
nice colors the driver also needs `red_balance`, `blue_balance` and
`white_balance_automatic` so we can tell the Arducam MCU to adjust
white balance. Without it the picture looks green under fluorescent
lights.

The next step is to add those controls and pass them through to the
MCU using the Pivariety control protocol (`CTRL_ID_REG` 0x0401 and
`CTRL_VALUE_REG` 0x0406).

There is also no ISP tuning file for this sensor on Jetson. So
`nvarguscamerasrc` does not work. The driver works only through raw
V4L2 right now.

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

## License

GPL-2.0 (same as Kurokesu's original). See `LICENSE`.
