#!/usr/bin/env bash
set -euo pipefail

for pattern in /dev/tty.usb* /dev/cu.usb* /dev/tty.wchusb* /dev/cu.wchusb*; do
  for port in $pattern; do
    [[ -e "$port" ]] && echo "$port"
  done
done

