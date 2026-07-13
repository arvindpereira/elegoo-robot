#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob

for pattern in /dev/tty.usb* /dev/cu.usb* /dev/tty.wchusb* /dev/cu.wchusb*; do
  for port in $pattern; do
    echo "$port"
  done
done
