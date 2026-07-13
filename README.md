# elegoo-robot

Custom low-level firmware experiments for the ELEGOO Conqueror Robot Tank Kit.

The first target is the UNO R3 controller. The ESP32/FPV camera module is left stock until the UNO pin map and movement control are confirmed.

## Layout

```text
docs/
  hardware.md              Hardware assumptions and pin-mapping notes
  protocol.md              Serial command protocol
  esp32-camera-and-control.md
                           ESP32 video/control protocol notes
firmware/
  uno/conqueror_serial/    Starter UNO firmware
  esp32-cam/               Placeholder for later camera/Wi-Fi firmware
vendor/elegoo/             Place downloaded ELEGOO product files here
```

## Quick Start

1. Download the official Conqueror product files from ELEGOO's Download Center:
   `STEM Kits -> Robot Kits -> Conqueror Robot Tank Kit -> Product Files`.
2. Put the downloaded archive or extracted files under `vendor/elegoo/`.
3. The current `config.h` is already updated from ELEGOO's `TB6612/ConquerorCar_TB6612_20240605` source.
4. Upload the starter firmware to the UNO.
5. Bench-test with the tracks lifted.

## Arduino IDE

Open:

```text
firmware/uno/conqueror_serial/conqueror_serial.ino
```

Select:

- Board: `Arduino Uno`
- Port: the USB serial port for the robot

Upload, then open Serial Monitor at `115200` baud.

Try:

```text
{P}
{M,80,80}
{S}
```

Lift the robot so the tracks are off the ground for the first motor tests.

## Arduino CLI

`arduino-cli` is optional, but the repo has a Makefile for it:

```bash
make compile
make ports
make upload PORT=/dev/tty.usbmodemXXXX
make monitor PORT=/dev/tty.usbmodemXXXX
```

## PlatformIO

`platformio.ini` is included for VS Code/PlatformIO users:

```bash
pio run
pio run -t upload --upload-port /dev/tty.usbmodemXXXX
pio device monitor -b 115200
```

## Current Status

- UNO serial tank-control scaffold exists.
- Motor pins are confirmed from ELEGOO's 2024 TB6612 source.
- Physical direction still needs a lifted-track bench test.
- ESP32-CAM firmware is intentionally not modified yet.
