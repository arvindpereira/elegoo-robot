# Serial Control Protocol

The starter UNO firmware listens on `Serial` at `115200` baud. Commands are framed with braces so they can be sent from Arduino Serial Monitor, a phone app DIY button, an ESP32-CAM bridge, or a Python script.

Examples:

```text
{P}
{S}
{F,120}
{B,120}
{L,90}
{R,90}
{M,120,80}
{T,750}
```

Commands:

| Command | Meaning |
| --- | --- |
| `{P}` | Ping. Replies with `{OK,PONG}`. |
| `{S}` | Stop both tracks. |
| `{F,speed}` | Forward, speed `0..255`. |
| `{B,speed}` | Backward, speed `0..255`. |
| `{L,speed}` | Spin left, speed `0..255`. |
| `{R,speed}` | Spin right, speed `0..255`. |
| `{M,left,right}` | Direct tank command. Each side is `-255..255`. |
| `{T,ms}` | Set command timeout in milliseconds. |

Safety behavior:

- If no valid movement command arrives before the timeout, the firmware stops both tracks.
- The default timeout is `1000 ms`.
- Speed values are clamped to the supported range.

