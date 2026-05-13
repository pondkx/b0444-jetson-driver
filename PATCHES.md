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
