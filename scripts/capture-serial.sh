#!/usr/bin/env bash
# Capture the liuqin USB debug CDC-ACM console mirror after a fastboot boot.
# The kernel mirrors its console ring (1 MiB) to the port identified by the
# pseudo-serial LIQDBG001; it only appears once XNU got far enough to bring up
# DWC3, so absence is itself a reading.
set -u
OUT_DIR=${1:?usage: capture-serial.sh <output-dir>}
WAIT_SECS=${2:-210}
CAPTURE_SECS=${3:-150}
mkdir -p "$OUT_DIR"

before=$(ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null | sort)
echo "[capture] started $(date +%H:%M:%S)  known ttys: ${before:-none}"

new=""
for ((i = 0; i < WAIT_SECS; i += 2)); do
  for t in $(ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null | sort); do
    case "$before" in **"$t"**) ;; *) new=$t; break ;; esac
  done
  [ -n "$new" ] && break
  sleep 2
done

if [ -z "$new" ]; then
  echo "[capture] no new CDC-ACM device within ${WAIT_SECS}s -- kernel never reached the USB debug port" | tee "$OUT_DIR/serial.txt"
  lsusb | grep -iE "18d1|qualcomm|xiaomi" >>"$OUT_DIR/serial.txt" 2>/dev/null
  exit 0
fi

echo "[capture] appeared: $new  at $(date +%H:%M:%S)"
stty -F "$new" 115200 raw -echo 2>/dev/null || true
timeout "$CAPTURE_SECS" cat "$new" > "$OUT_DIR/serial.txt" 2>"$OUT_DIR/serial.err"
echo "[capture] bytes captured: $(wc -c <"$OUT_DIR/serial.txt")  at $(date +%H:%M:%S)"
