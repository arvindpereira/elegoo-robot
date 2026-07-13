# Mac SLAM Client Plan

Goal: build a macOS application that connects directly to the ELEGOO Conqueror robot, streams camera video, drives the tank, and records enough synchronized data to experiment with monocular SLAM.

This plan starts by using the stock ELEGOO ESP32 and UNO firmware protocols. Reflashing is deferred until we prove the stock bridge is insufficient.

## Known Robot Interfaces

The ESP32 camera module creates an access point:

```text
SSID: ELEGOO-<chipid>
Password: blank
Robot IP: 192.168.4.1
```

Video:

```text
MJPEG stream: http://192.168.4.1:81/stream
Single frame:  http://192.168.4.1/capture
Status JSON:   http://192.168.4.1/status
Control API:   http://192.168.4.1/control?var=<name>&val=<value>
```

Drive/control:

```text
TCP socket: 192.168.4.1:100
```

The TCP socket sends `{Heartbeat}` once per second. The client must reply with `{Heartbeat}`. If the heartbeat fails or the client disconnects, the ESP32 sends `{"N":100}` to the UNO to stop/clear the active mode.

Manual drive command:

```json
{"N":102,"D1":1,"D2":120}
```

`D1` direction values:

| Value | Direction |
| --- | --- |
| `1` | Forward |
| `2` | Backward |
| `3` | Turn left |
| `4` | Turn right |
| `5` | Left forward |
| `6` | Left backward |
| `7` | Right forward |
| `8` | Right backward |
| `9` | Stop |

## Architecture

The first version should be split into four modules:

| Module | Responsibility |
| --- | --- |
| `robot_control` | TCP connection, heartbeat, command encoding, safety stop |
| `video_stream` | MJPEG HTTP connection, JPEG extraction, frame timestamps |
| `teleop_ui` | Keyboard/gamepad controls and live video preview |
| `recording` | Save video frames, command stream, timestamps, and metadata |

The SLAM layer should be a separate later module:

| Module | Responsibility |
| --- | --- |
| `slam_adapter` | Feed decoded frames into selected SLAM backend |

This separation matters because teleop and recording can be validated before SLAM complexity is introduced.

## Phase 1: Connectivity Probe

Build a small command-line tool first.

Deliverables:

- Connect to `192.168.4.1:100`.
- Print incoming socket messages.
- Reply to `{Heartbeat}`.
- Send a stop command on startup and shutdown:

```json
{"N":100}
```

- Send one short movement command with the tracks lifted:

```json
{"N":102,"D1":1,"D2":80}
{"N":102,"D1":9,"D2":0}
```

Validation:

- Robot does not move unless commanded.
- Robot stops if the client exits.
- Heartbeat can run for at least 60 seconds without disconnecting.

Safety rule:

- Every control process must send stop on startup, normal shutdown, error, and `SIGINT`.

## Phase 2: Video Probe

Build a video-only probe.

Deliverables:

- Connect to `http://192.168.4.1:81/stream`.
- Parse the multipart MJPEG stream.
- Decode JPEG frames.
- Display frame rate, resolution, and average inter-frame time.
- Save a 10 second sample to disk.

Validation:

- Stream remains stable for at least 5 minutes.
- Dropped/invalid JPEG frames are counted, not fatal.
- Timestamps are monotonic using local receive time.

Initial implementation:

```bash
python3 tools/video_probe.py --duration 10
```

The tool does not open the robot control socket and does not send drive commands.

Notes:

- The ESP32 stream is MJPEG, not a low-latency robotics camera protocol.
- Use local monotonic timestamps when each complete JPEG is received.
- Later we can include HTTP `X-Timestamp` headers if available per frame, but local monotonic time is the first reliable baseline.

## Phase 3: Teleoperation App

Combine TCP control and video preview.

Deliverables:

- Live camera preview.
- Keyboard controls:

| Key | Command |
| --- | --- |
| `W` | Forward |
| `S` | Backward |
| `A` | Turn left |
| `D` | Turn right |
| `Space` | Stop |
| `Q` | Quit after stop |

- Speed control with a fixed initial speed, for example `100`.
- Dead-man behavior: if no key/control update is sent within a short interval, send stop.
- Visible connection state for video, control socket, and heartbeat.

Validation:

- Stop works immediately.
- Closing the app stops the robot.
- Video remains visible while driving.

## Phase 4: Recording Format

Before SLAM, record clean datasets.

Directory layout:

```text
recordings/
  2026-xx-xxTxx-xx-xx/
    metadata.json
    frames/
      000000.jpg
      000001.jpg
    frames.csv
    commands.csv
```

`metadata.json`:

```json
{
  "robot_ip": "192.168.4.1",
  "video_url": "http://192.168.4.1:81/stream",
  "control_host": "192.168.4.1",
  "control_port": 100,
  "camera_settings": {
    "framesize": null,
    "quality": null
  }
}
```

`frames.csv`:

```text
frame_id,monotonic_ns,filename,width,height,decode_ok
0,123456789,frames/000000.jpg,800,600,true
```

`commands.csv`:

```text
monotonic_ns,command_json
123456000,"{""N"":102,""D1"":1,""D2"":100}"
```

Validation:

- A recording can be replayed offline without the robot.
- Frame timestamps and command timestamps share the same monotonic clock.
- Recording continues if an occasional frame is corrupt.

## Phase 5: Camera Calibration

Monocular SLAM needs camera intrinsics.

Deliverables:

- Capture calibration images from the ESP32 stream.
- Use a printed checkerboard or Charuco board.
- Estimate:
  - focal lengths
  - principal point
  - distortion coefficients
  - image resolution used during SLAM

Store calibration:

```text
calibration/
  esp32_camera_<resolution>.yaml
```

Validation:

- Reprojection error is documented.
- Calibration resolution matches runtime stream resolution.
- If `framesize` changes, create a new calibration file.

## Phase 6: SLAM Backend

Evaluate monocular SLAM after recording and calibration work.

Candidate approaches:

| Option | Role |
| --- | --- |
| OpenCV visual odometry prototype | Simple first baseline |
| ORB-SLAM3 | More capable monocular SLAM, more setup work |
| RTAB-Map | Useful if we later add depth or stereo |
| OpenVSLAM forks | Possible but maintenance varies |

Recommended path:

1. Start with offline frame playback into a simple OpenCV visual-odometry baseline.
2. Validate feature tracking on recorded data.
3. Move to ORB-SLAM3 only after frame quality and calibration are acceptable.

Expected limitations:

- Monocular SLAM has scale ambiguity.
- Smooth floors and blank walls can break tracking.
- Motion blur from fast turns will hurt tracking.
- The robot currently exposes no wheel encoder odometry through the documented protocol.

Mitigations:

- Drive slowly.
- Prefer textured routes.
- Add stop-and-turn scan behaviors.
- Later add wheel odometry or IMU data from the UNO if needed.

## Phase 7: Optional Firmware Changes

Only consider firmware changes after the stock protocol is validated.

Possible UNO changes:

- Expose battery voltage, ultrasonic distance, line sensors, and IMU data in a cleaner telemetry stream.
- Add direct signed left/right track speed commands.
- Add a watchdog stop independent of the ESP32 heartbeat.

Possible ESP32 changes:

- Add WebSocket control instead of raw TCP.
- Add timestamps to each frame/control event.
- Add a single HTTP endpoint that reports camera configuration.
- Make AP password configurable.

Do not reflash ESP32 until:

- Stock video streaming works from the Mac.
- Stock TCP command bridge works from the Mac.
- We know the factory firmware can be restored.

## Immediate Next Task

Build `tools/robot_probe`, a command-line probe that:

1. Connects to `192.168.4.1:100`.
2. Replies to `{Heartbeat}`.
3. Sends no movement commands in default mode.
4. Optionally sends `{"N":100}` only when explicitly run with a stop/clear flag.
5. Later accepts simple keyboard or terminal commands:
   - `w`: forward
   - `s`: backward
   - `a`: left
   - `d`: right
   - space or `x`: stop
   - `q`: stop and quit
6. Logs all sent and received messages.

After that, build `tools/video_probe` for the MJPEG stream.
