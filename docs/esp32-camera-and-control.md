# ESP32 Camera and Control Notes

The ELEGOO archive includes source for the camera module:

```text
vendor/elegoo/ELEGOO Conqueror Robot Tank Kit 2024.06.05.zip
  ELEGOO Conqueror Robot Tank Kit 2024.06.05/
    02 Manual & Main Code & APP/
      04 Code of Carmer (ESP32)/
        ESP32-S3-WROOM-1-Camera/
          ESP32_CameraServer_AP_2023_V1.3/
```

The current source targets:

```text
Board: ESP32S3 Dev Module
USB CDC On Boot: Enabled
Flash Size: 8MB (64Mb)
Partition Scheme: 8M with spiffs (3MB APP/1.5MB SPIFFS)
PSRAM: OPI PSRAM
```

## Network

The ESP32 runs as a Wi-Fi access point.

- SSID prefix: `ELEGOO-`
- SSID suffix: chip ID
- IP: `192.168.4.1`
- Password: blank/open AP in the shipped `CameraWebServer_AP.h`

The source still has a commented old password value, `elegoo2020`, but the active password is `""`.

## Video HTTP Endpoints

Registered by `app_httpd.cpp`:

| Endpoint | Port | Meaning |
| --- | --- | --- |
| `/` | `80` | Built-in camera web UI |
| `/status` | `80` | Camera sensor status JSON |
| `/control?var=<name>&val=<value>` | `80` | Camera setting control |
| `/capture` | `80` | Single JPEG frame |
| `/Test` | `80` | Alias wired to the stream handler |
| `/stream` | `81` | MJPEG stream |

Useful URLs:

```text
http://192.168.4.1/
http://192.168.4.1/status
http://192.168.4.1/capture
http://192.168.4.1:81/stream
http://192.168.4.1/control?var=framesize&val=3
```

## Robot Control TCP Bridge

The ESP32 source opens a TCP server:

```cpp
WiFiServer server(100);
```

A client connects to:

```text
192.168.4.1:100
```

The TCP socket uses brace-framed messages. The ESP32:

- Reads messages from the TCP client.
- Replies to `{Heartbeat}` locally.
- Forwards other brace-framed messages to the UNO over `Serial2`.
- Uses `Serial2.begin(9600, SERIAL_8N1, RXD2, TXD2)` with `RXD2 = 3`, `TXD2 = 40`.
- Sends `{"N":100}` to stop/clear the car when the client disconnects or the heartbeat fails.

Heartbeat behavior:

- ESP32 sends `{Heartbeat}` to the TCP client once per second.
- The client should reply with `{Heartbeat}`.
- If several heartbeats are missed, ESP32 disconnects and sends stop/clear to the UNO.

## ELEGOO JSON Protocol

The command protocol is documented in:

```text
04 Related chip information/Communication protocol for Conqueror.pdf
```

The UNO parser is implemented in:

```text
02 Main Program/TB6612/ConquerorCar_TB6612_20240605/ApplicationFunctionSet_xxx0.cpp
```

Commands are JSON objects inside braces. Examples:

```json
{"N":100}
{"N":102,"D1":1,"D2":120}
{"N":102,"D1":9,"D2":0}
{"N":3,"D1":3,"D2":120}
{"N":4,"D1":120,"D2":120}
{"N":106,"D1":1}
```

Important commands:

| Command | Meaning |
| --- | --- |
| `{"N":100}` | Clear/stop, standby mode |
| `{"N":101,"D1":1}` | Switch to line-tracking mode |
| `{"N":101,"D1":2}` | Switch to obstacle-avoidance mode |
| `{"N":101,"D1":3}` | Switch to follow mode |
| `{"N":102,"D1":dir,"D2":speed}` | Rocker/manual movement |
| `{"N":106,"D1":dir}` | Camera servo movement |
| `{"N":21,"D1":1}` | Obstacle detected query |
| `{"N":21,"D1":2}` | Ultrasonic distance query |
| `{"N":22,"D1":sensor}` | Line sensor query |

Rocker `D1` values for `N=102`:

| `D1` | Direction |
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

Camera servo `D1` values for `N=106`:

| `D1` | Direction |
| --- | --- |
| `1` | Up |
| `2` | Down |
| `3` | Left |
| `4` | Right |

## Mac App Direction

A first Mac-side prototype can keep ELEGOO firmware unchanged:

1. Join the ESP32 AP from macOS.
2. Pull MJPEG frames from `http://192.168.4.1:81/stream`.
3. Open a TCP socket to `192.168.4.1:100`.
4. Reply to `{Heartbeat}` messages.
5. Send `N=102` movement commands for teleop.
6. Feed decoded frames into a SLAM pipeline.

This should be validated before reflashing the ESP32 or replacing the UNO firmware.

For monocular SLAM, the camera stream is likely usable for a prototype, but quality constraints matter:

- MJPEG timing may be jittery.
- Camera intrinsics and distortion coefficients need calibration.
- The robot has no wheel encoders in the documented interface, so scale will be weak unless fused with external constraints or added sensors.
- Slow constant-speed motion and good floor texture will matter.
