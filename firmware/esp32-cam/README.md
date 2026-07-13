# ESP32-CAM Firmware

This folder is intentionally empty for now.

The first development phase targets the UNO R3 because it directly controls the tracks and sensors. Once that is working, the ESP32-CAM can become a custom Wi-Fi/video bridge that sends the UNO commands documented in `docs/protocol.md`.

Possible later options:

- Keep ELEGOO's camera firmware and use the app/DIY serial bridge.
- Replace it with Arduino-ESP32 firmware that serves a simple web UI.
- Replace it with ESP-IDF firmware for more control over networking and camera streaming.

