# elegoo-robot

Tools and firmware experiments for the ELEGOO Conqueror Robot Tank Kit.

The current workflow keeps the stock ESP32 camera/control firmware in place and connects to it directly from a Mac. This lets us stream video, log synchronized data, and eventually drive the robot while collecting datasets for monocular SLAM.

## Current Capabilities

- Connect to the ESP32 Wi-Fi access point.
- Read the camera stream from `http://192.168.4.1:81/stream`.
- Connect to the robot control socket at `192.168.4.1:100`.
- Reply to stock `{Heartbeat}` messages.
- Record synchronized video frames and control events.
- Replay and inspect recorded sessions offline.
- Optionally drive from the keyboard while recording.

## Safety

Default tools do not drive the robot. The browser app starts disarmed and asks for confirmation before enabling drive commands.

Before arming drive:

1. Put the robot on the floor with clear space, or lift the tracks for bench testing.
2. Connect the main battery if you expect the tracks to move.
3. Keep a hand near the power switch.
4. Start with a low speed, for example `--speed 80`.

The browser and terminal teleop tools send stop commands when they exit and have dead-man stop timers while driving.

## Robot Network

Join the ESP32 Wi-Fi network from macOS:

```text
SSID: ELEGOO-<chipid>
Password: blank
Robot IP: 192.168.4.1
```

Useful endpoints:

```text
Camera status:  http://192.168.4.1/status
Single JPEG:    http://192.168.4.1/capture
MJPEG stream:   http://192.168.4.1:81/stream
Control socket: 192.168.4.1:100
```

## Recommended First Run

From the repo root:

```bash
cd /Users/arvind/code/elegoo-robot
```

Check video without driving:

```bash
python3 tools/video_probe.py --duration 5
```

Check the control heartbeat without driving:

```bash
python3 tools/robot_probe.py --duration 6
```

Record a synchronized no-driving session:

```bash
python3 tools/session_recorder.py --duration 10
```

Inspect the newest recording:

```bash
python3 tools/playback_session.py recordings/<timestamp> --validate-files
```

## Browser App

Start the local browser app:

```bash
scripts/run_web_teleop.sh
```

This opens a local page at:

```text
http://127.0.0.1:8765/
```

The browser shows the robot camera stream. The local server records the same JPEG frames it forwards to the browser, so the session can be inspected later with `tools/playback_session.py`.

The browser app starts with drive disarmed. Use the `Arm Drive` button in the UI to enable movement. A warning dialog asks you to confirm that the robot is in a safe place before movement is enabled.

If you want a no-drive demo mode where the browser cannot arm driving at all:

```bash
scripts/run_web_teleop.sh --lock-drive
```

Browser app controls:

| Key | Action |
| --- | --- |
| `w` or `↑` | Forward |
| `s` or `↓` | Backward |
| `a` or `←` | Turn left |
| `d` or `→` | Turn right |
| `space` or `x` | Stop |
| `+` / `-` | Adjust speed |
| `i` / `k` | Camera pitch up/down by 5 degrees |
| `j` / `l` | Camera yaw left/right by 5 degrees |
| `c` | Center camera |

Browser buttons:

| Button | Action |
| --- | --- |
| `Arm Drive` / `Disarm Drive` | Runtime drive enable/disable. Arming shows a safety confirmation dialog. |
| `Stop` | Immediately send a stop command. This is fixed in the bottom control bar. |
| `Manual` | Clear autonomous mode and return to keyboard/manual control. |
| `Standby` | Send the stock stop/clear command. |
| `Line` | Switch to line-tracking mode. Requires drive to be armed. |
| `Obstacle` | Switch to obstacle-avoidance mode. Requires drive to be armed. |
| `Follow` | Switch to follow mode. Requires drive to be armed. |
| `Center Camera` | Return pitch/yaw to center. |
| `Stop Server` | End the local app and write the session summary. This is fixed in the bottom control bar. |

## Teleop Recorder

Start in safe mode first. This records video/control events and ignores drive keys:

```bash
python3 tools/teleop_recorder.py
```

Keys:

| Key | Action |
| --- | --- |
| `w` | Forward |
| `s` | Backward |
| `a` | Turn left |
| `d` | Turn right |
| `q` | Right-forward diagonal |
| `e` | Left-forward diagonal |
| `space` or `x` | Stop |
| `+` / `-` | Adjust speed |
| `r` | Print status |
| `Ctrl-C` | Stop and quit |

In safe mode, drive keys are logged as ignored events. To actually drive and record:

```bash
python3 tools/teleop_recorder.py --enable-drive --speed 80
```

Recordings are written to:

```text
recordings/<timestamp>/
  metadata.json
  summary.json
  frames.csv
  control_events.csv
  frames/
    000000.jpg
    000001.jpg
```

`recordings/` is ignored by Git.

## Offline Playback

Summarize a recorded session:

```bash
python3 tools/playback_session.py recordings/<timestamp> --validate-files
```

Replay the frame/control event timeline as text:

```bash
python3 tools/playback_session.py recordings/<timestamp> --replay --speed 4
```

The playback tool does not connect to the robot.

## Tool Reference

| Tool | Purpose | Sends movement commands |
| --- | --- | --- |
| `tools/video_probe.py` | Test and record the MJPEG stream | No |
| `tools/robot_probe.py` | Test heartbeat/control socket | No |
| `tools/session_recorder.py` | Record video plus heartbeat events | No |
| `tools/playback_session.py` | Inspect recordings offline | No |
| `tools/teleop_recorder.py` | Record while using keyboard controls | Only with `--enable-drive` |
| `tools/web_teleop_app.py` | Browser video/control app with recording | Only after UI arming |

## Firmware Notes

The repo also contains a starter UNO firmware project:

```text
firmware/uno/conqueror_serial/
```

The motor pins are confirmed from ELEGOO's 2024 TB6612 source. We have not needed to replace the stock UNO firmware for the Mac-side video/control workflow.

Compile the UNO sketch:

```bash
make compile
```

Upload only when you intentionally want to replace the current UNO firmware:

```bash
make upload PORT=/dev/cu.usbserial-XXXX
```

## Docs

- `docs/esp32-camera-and-control.md`: ESP32 HTTP endpoints, TCP bridge, and JSON protocol.
- `docs/mac-slam-client-plan.md`: phased plan for recording, calibration, teleop, and SLAM.
- `docs/hardware.md`: confirmed pin mapping and hardware notes.
- `docs/elegoo-source.md`: where the useful ELEGOO files live in the vendor archive.
