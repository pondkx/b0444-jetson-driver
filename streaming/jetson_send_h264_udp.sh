#!/bin/bash
# Stream the B0444 over UDP to a viewer on another computer.
# Use this to tune the camera live without the Jetson HDMI hassle.
#
# Default: send to MAC_IP=192.168.1.10 port 5000
# Override: MAC_IP=192.168.1.42 ./jetson_send_h264_udp.sh
#
# Tip: set sensible v4l2 controls first, then start this script:
#   sudo v4l2-ctl -d /dev/video0 -c gain=90 -c exposure=12000
#   sudo v4l2-ctl -d /dev/v4l-subdev1 -c red_balance=1550 -c blue_balance=1650

set -e

MAC_IP="${MAC_IP:-192.168.1.10}"
PORT="${PORT:-5000}"
WIDTH="${WIDTH:-1920}"
HEIGHT="${HEIGHT:-1080}"
FPS="${FPS:-15}"
BITRATE="${BITRATE:-4000}"

echo "Streaming B0444 -> $MAC_IP:$PORT ($WIDTH x $HEIGHT @ $FPS fps, $BITRATE kbps H.264)"
echo "Ctrl-C to stop."

# bayer2rgb only does 8-bit Bayer. Our sensor outputs RG10 (10-bit in 16-bit).
# We use a small chain: v4l2src reads raw, videoconvert drops to 8-bit,
# bayer2rgb debayers, then x264 encodes for UDP.
#
# If you have RidgeRun's rrcudadebayer, this can be done on GPU instead.
sudo gst-launch-1.0 -v \
  v4l2src device=/dev/video0 io-mode=mmap \
  ! "video/x-bayer,format=rggb,width=${WIDTH},height=${HEIGHT},framerate=${FPS}/1" \
  ! bayer2rgb \
  ! videoconvert ! "video/x-raw,format=I420" \
  ! x264enc tune=zerolatency bitrate=${BITRATE} speed-preset=ultrafast key-int-max=30 \
  ! rtph264pay config-interval=1 pt=96 \
  ! udpsink host=${MAC_IP} port=${PORT} sync=false
