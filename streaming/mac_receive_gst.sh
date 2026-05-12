#!/bin/bash
# Listen for B0444 H.264 stream from the Jetson on UDP port 5000.
# Needs GStreamer on the Mac:
#   brew install gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-libav

PORT="${PORT:-5000}"

echo "Listening for B0444 H.264 stream on UDP $PORT..."

gst-launch-1.0 -v \
  udpsrc port=${PORT} \
  caps="application/x-rtp,media=video,clock-rate=90000,encoding-name=H264,payload=96" \
  ! rtpjitterbuffer latency=50 ! rtph264depay ! h264parse ! avdec_h264 \
  ! videoconvert ! autovideosink sync=false
