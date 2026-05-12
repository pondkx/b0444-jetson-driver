# Live streaming B0444 to your Mac

The Jetson HDMI is sometimes flaky with the NVIDIA compositor (the
`nvbufsurftransform` / EGL bug can wedge the display). So a much safer
way to "see" the camera while you tune it is to stream over UDP to
another computer on the same network.

## On the Jetson

```
./jetson_send_h264_udp.sh
```

Default sends to `192.168.1.10` on UDP port `5000`. Override:

```
MAC_IP=192.168.1.42 PORT=5000 ./jetson_send_h264_udp.sh
```

The script uses GStreamer's `bayer2rgb` (8-bit Bayer software debayer),
then `x264enc` with `tune=zerolatency`. End-to-end latency on a local
LAN is around 80-150 ms with these settings.

## On the Mac (two options)

### Option 1: GStreamer (lowest latency)

Install once:
```
brew install gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-libav
```

Run the receiver:
```
./mac_receive_gst.sh
```

A window opens showing the live B0444 feed.

### Option 2: VLC (easiest, slightly higher latency)

```
open -a VLC b0444.sdp
```

(If VLC asks, allow incoming connections.)

## Tuning while you stream

Open a second SSH session to the Jetson and tweak controls live. The
pipeline picks up the new values on the next frame, no restart needed:

```
sudo v4l2-ctl -d /dev/video0 -c gain=120
sudo v4l2-ctl -d /dev/video0 -c exposure=20000
sudo v4l2-ctl -d /dev/v4l-subdev1 -c red_balance=1700
sudo v4l2-ctl -d /dev/v4l-subdev1 -c blue_balance=1500
```

## Why H.264 and not raw

Raw 1080p30 over the network is ~750 Mbps. Wi-Fi will not survive it.
H.264 brings that down to 4-8 Mbps with imperceptible quality loss for
preview purposes. If you need lossless, use MJPEG (heavier) or just
capture frames to disk and SCP them.
