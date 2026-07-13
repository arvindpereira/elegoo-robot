#!/usr/bin/env python3
"""Terminal teleoperation recorder for the ELEGOO Conqueror robot.

The app records the ESP32 MJPEG stream and control events in the same format as
`session_recorder.py`, so `playback_session.py` can inspect the output.

Driving is disabled unless `--enable-drive` is provided.
"""

from __future__ import annotations

import argparse
import csv
import json
import select
import signal
import socket
import sys
import termios
import threading
import time
import tty
from pathlib import Path
from typing import BinaryIO
from urllib.error import URLError
from urllib.request import Request, urlopen

from video_probe import DEFAULT_STATUS_URL, DEFAULT_STREAM_URL, fetch_status, jpeg_dimensions, utc_stamp


DEFAULT_CONTROL_HOST = "192.168.4.1"
DEFAULT_CONTROL_PORT = 100
HEARTBEAT = b"{Heartbeat}"
STOP_COMMAND = '{"N":100}'
SOI = b"\xff\xd8"
EOI = b"\xff\xd9"

KEY_COMMANDS = {
    "w": ("forward", {"N": 102, "D1": 1, "D2": None}),
    "s": ("backward", {"N": 102, "D1": 2, "D2": None}),
    "a": ("left", {"N": 102, "D1": 3, "D2": None}),
    "d": ("right", {"N": 102, "D1": 4, "D2": None}),
    "e": ("left_forward", {"N": 102, "D1": 5, "D2": None}),
    "q": ("right_forward", {"N": 102, "D1": 7, "D2": None}),
    "x": ("stop", {"N": 102, "D1": 9, "D2": 0}),
    " ": ("stop", {"N": 102, "D1": 9, "D2": 0}),
}


stop_event = threading.Event()


def handle_stop(signum: int, frame: object) -> None:
  del signum, frame
  stop_event.set()


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(
      description="Record camera data while optionally driving the robot from the keyboard."
  )
  parser.add_argument("--video-url", default=DEFAULT_STREAM_URL, help="MJPEG stream URL.")
  parser.add_argument("--status-url", default=DEFAULT_STATUS_URL, help="Camera status URL.")
  parser.add_argument("--control-host", default=DEFAULT_CONTROL_HOST, help="ESP32 control host.")
  parser.add_argument("--control-port", type=int, default=DEFAULT_CONTROL_PORT, help="ESP32 control port.")
  parser.add_argument("--duration", type=float, default=0.0, help="Seconds to run. 0 means until quit.")
  parser.add_argument("--output-dir", type=Path, default=None, help="Recording directory.")
  parser.add_argument("--speed", type=int, default=100, help="Drive speed, 0..255.")
  parser.add_argument("--deadman-ms", type=int, default=500, help="Stop if no drive key is sent within this window.")
  parser.add_argument("--enable-drive", action="store_true", help="Allow movement commands to be sent.")
  parser.add_argument("--no-keyboard", action="store_true", help="Record without reading keyboard input.")
  parser.add_argument("--no-save-frames", action="store_true", help="Record frame metadata without JPEG files.")
  parser.add_argument("--send-stop-on-connect", action="store_true", help="Send {\"N\":100} immediately after connecting.")
  parser.add_argument("--chunk-size", type=int, default=8192, help="HTTP read chunk size.")
  parser.add_argument("--report-every", type=int, default=30, help="Print video progress every N frames.")
  return parser.parse_args()


def clamp_speed(speed: int) -> int:
  return max(0, min(255, speed))


def encode_command(command: dict[str, int | None], speed: int) -> str:
  payload = dict(command)
  if payload.get("D2") is None:
    payload["D2"] = speed
  return json.dumps(payload, separators=(",", ":"))


def iter_jpegs(response: BinaryIO, chunk_size: int):
  buffer = bytearray()
  while not stop_event.is_set():
    chunk = response.read(chunk_size)
    if not chunk:
      break
    buffer.extend(chunk)

    while True:
      start = buffer.find(SOI)
      if start < 0:
        if len(buffer) > chunk_size * 4:
          del buffer[:-chunk_size]
        break
      end = buffer.find(EOI, start + 2)
      if end < 0:
        if start > 0:
          del buffer[:start]
        break
      frame = bytes(buffer[start:end + 2])
      del buffer[:end + 2]
      yield frame


def prepare_recording(args: argparse.Namespace, status: dict[str, object] | None) -> Path:
  output_dir = args.output_dir or Path("recordings") / utc_stamp()
  (output_dir / "frames").mkdir(parents=True, exist_ok=True)
  metadata = {
      "created_utc": utc_stamp(),
      "video_url": args.video_url,
      "status_url": args.status_url,
      "control_host": args.control_host,
      "control_port": args.control_port,
      "duration_seconds": args.duration,
      "drive_enabled": args.enable_drive,
      "drive_speed": clamp_speed(args.speed),
      "deadman_ms": args.deadman_ms,
      "save_frames": not args.no_save_frames,
      "camera_status": status,
  }
  (output_dir / "metadata.json").write_text(
      json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
  )
  return output_dir


def write_control_event(writer: csv.DictWriter, event: str, payload: str = "") -> None:
  writer.writerow({
      "monotonic_ns": time.monotonic_ns(),
      "event": event,
      "payload": payload,
  })


class ControlClient:
  def __init__(self, args: argparse.Namespace, output_dir: Path, summary: dict[str, int | str]):
    self.args = args
    self.output_dir = output_dir
    self.summary = summary
    self.sock: socket.socket | None = None
    self.writer: csv.DictWriter | None = None
    self.csv_file = None
    self.lock = threading.Lock()
    self.connected = threading.Event()

  def __enter__(self) -> "ControlClient":
    self.csv_file = (self.output_dir / "control_events.csv").open("w", newline="", encoding="utf-8")
    self.writer = csv.DictWriter(self.csv_file, fieldnames=["monotonic_ns", "event", "payload"])
    self.writer.writeheader()
    return self

  def __exit__(self, exc_type, exc, tb) -> None:
    del exc_type, exc, tb
    if self.csv_file is not None:
      self.csv_file.close()

  def log(self, event: str, payload: str = "") -> None:
    if self.writer is not None:
      self.writer.writerow({
          "monotonic_ns": time.monotonic_ns(),
          "event": event,
          "payload": payload,
      })

  def send_raw(self, payload: str, event: str) -> None:
    with self.lock:
      if self.sock is None:
        self.log("send_failed", payload)
        return
      self.sock.sendall(payload.encode("utf-8"))
      self.log(event, payload)

  def run(self) -> None:
    try:
      with socket.create_connection((self.args.control_host, self.args.control_port), timeout=5) as sock:
        self.sock = sock
        sock.settimeout(1)
        self.connected.set()
        self.summary["control_status"] = "connected"
        self.log("connected", f"{self.args.control_host}:{self.args.control_port}")

        if self.args.send_stop_on_connect:
          self.send_raw(STOP_COMMAND, "sent_stop")
          self.summary["stop_commands_sent"] = int(self.summary.get("stop_commands_sent", 0)) + 1

        buffer = bytearray()
        while not stop_event.is_set():
          try:
            data = sock.recv(1024)
          except socket.timeout:
            continue
          if not data:
            self.log("disconnected_by_peer")
            break
          self.summary["control_bytes_received"] = int(self.summary.get("control_bytes_received", 0)) + len(data)
          buffer.extend(data)
          self.process_buffer(buffer)
    except OSError as exc:
      self.summary["control_status"] = "error"
      self.summary["control_error"] = str(exc)
      self.log("control_error", str(exc))
    finally:
      self.connected.set()
      self.sock = None

  def process_buffer(self, buffer: bytearray) -> None:
    while True:
      start = buffer.find(b"{")
      if start < 0:
        buffer.clear()
        return
      if start > 0:
        del buffer[:start]
      end = buffer.find(b"}", 1)
      if end < 0:
        return
      message = bytes(buffer[:end + 1])
      del buffer[:end + 1]
      text = message.decode("utf-8", "replace")
      self.log("recv", text)
      if message == HEARTBEAT:
        self.send_raw(HEARTBEAT.decode("ascii"), "sent_heartbeat")
        self.summary["heartbeats_received"] = int(self.summary.get("heartbeats_received", 0)) + 1
        self.summary["heartbeats_sent"] = int(self.summary.get("heartbeats_sent", 0)) + 1


def video_worker(args: argparse.Namespace, output_dir: Path, summary: dict[str, int | str]) -> None:
  frames_dir = output_dir / "frames"
  with (output_dir / "frames.csv").open("w", newline="", encoding="utf-8") as csv_file:
    writer = csv.DictWriter(
        csv_file,
        fieldnames=["frame_id", "monotonic_ns", "filename", "width", "height", "byte_count", "decode_ok"],
    )
    writer.writeheader()
    try:
      request = Request(args.video_url, headers={"User-Agent": "elegoo-teleop-recorder/0.1"})
      with urlopen(request, timeout=5) as response:
        summary["video_content_type"] = response.headers.get("Content-Type", "")
        for frame_id, frame in enumerate(iter_jpegs(response, args.chunk_size)):
          width, height = jpeg_dimensions(frame)
          filename = ""
          if not args.no_save_frames:
            filename = f"frames/{frame_id:06d}.jpg"
            (frames_dir / f"{frame_id:06d}.jpg").write_bytes(frame)
          writer.writerow({
              "frame_id": frame_id,
              "monotonic_ns": time.monotonic_ns(),
              "filename": filename,
              "width": width if width is not None else "",
              "height": height if height is not None else "",
              "byte_count": len(frame),
              "decode_ok": str(width is not None and height is not None).lower(),
          })
          summary["frames"] = frame_id + 1
          summary["last_width"] = width or ""
          summary["last_height"] = height or ""
          if args.report_every > 0 and (frame_id + 1) % args.report_every == 0:
            print(f"\rframes={frame_id + 1} last={width}x{height}", end="", flush=True)
    except URLError as exc:
      summary["video_status"] = "error"
      summary["video_error"] = str(exc)
      stop_event.set()
      return
  summary["video_status"] = "ok"


class RawTerminal:
  def __enter__(self):
    self.fd = sys.stdin.fileno()
    self.old = termios.tcgetattr(self.fd)
    tty.setcbreak(self.fd)
    return self

  def __exit__(self, exc_type, exc, tb):
    del exc_type, exc, tb
    termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)


def read_key(timeout: float = 0.1) -> str | None:
  readable, _, _ = select.select([sys.stdin], [], [], timeout)
  if not readable:
    return None
  return sys.stdin.read(1)


def teleop_loop(args: argparse.Namespace, control: ControlClient, summary: dict[str, int | str]) -> None:
  speed = clamp_speed(args.speed)
  last_drive_ns = 0
  deadline = time.monotonic() + args.duration if args.duration > 0 else None
  deadman_ns = max(100, args.deadman_ms) * 1_000_000

  print()
  print("Teleop recorder")
  print("Keys: w/s/a/d drive, q/e diagonals, space or x stop, +/- speed, r status, Ctrl-C quit")
  print(f"drive_enabled={args.enable_drive} speed={speed}")
  if not args.enable_drive:
    print("Drive commands are disabled. Re-run with --enable-drive to send movement commands.")

  if args.no_keyboard:
    while not stop_event.is_set():
      if deadline is not None and time.monotonic() >= deadline:
        stop_event.set()
        break
      time.sleep(0.1)
    return

  with RawTerminal():
    while not stop_event.is_set():
      if deadline is not None and time.monotonic() >= deadline:
        stop_event.set()
        break

      key = read_key(0.05)
      now_ns = time.monotonic_ns()

      if args.enable_drive and last_drive_ns and now_ns - last_drive_ns > deadman_ns:
        control.send_raw(encode_command(KEY_COMMANDS["x"][1], speed), "sent_command")
        summary["commands_sent"] = int(summary.get("commands_sent", 0)) + 1
        summary["deadman_stops"] = int(summary.get("deadman_stops", 0)) + 1
        last_drive_ns = 0
        print("\rdeadman stop sent            ", end="", flush=True)

      if key is None:
        continue
      if key == "\x03":
        stop_event.set()
        break
      if key in ("+", "="):
        speed = clamp_speed(speed + 10)
        print(f"\rspeed={speed}                ", end="", flush=True)
        continue
      if key in ("-", "_"):
        speed = clamp_speed(speed - 10)
        print(f"\rspeed={speed}                ", end="", flush=True)
        continue
      if key == "r":
        print(
            f"\rframes={summary.get('frames', 0)} heartbeats={summary.get('heartbeats_received', 0)} speed={speed}      ",
            end="",
            flush=True,
        )
        continue

      if key not in KEY_COMMANDS:
        continue

      label, command = KEY_COMMANDS[key]
      payload = encode_command(command, speed)
      if args.enable_drive:
        control.send_raw(payload, "sent_command")
        summary["commands_sent"] = int(summary.get("commands_sent", 0)) + 1
        if label == "stop":
          last_drive_ns = 0
        else:
          last_drive_ns = now_ns
        print(f"\r{label} sent speed={speed}       ", end="", flush=True)
      else:
        control.log("drive_key_ignored", f"{label}:{payload}")
        print(f"\r{label} ignored; drive disabled       ", end="", flush=True)

  if args.enable_drive:
    control.send_raw(encode_command(KEY_COMMANDS["x"][1], speed), "sent_command")
    summary["commands_sent"] = int(summary.get("commands_sent", 0)) + 1
    print("\nshutdown stop sent")


def main() -> int:
  signal.signal(signal.SIGINT, handle_stop)
  signal.signal(signal.SIGTERM, handle_stop)
  args = parse_args()
  args.speed = clamp_speed(args.speed)

  status = fetch_status(args.status_url)
  output_dir = prepare_recording(args, status)
  summary: dict[str, int | str] = {
      "started_monotonic_ns": time.monotonic_ns(),
      "frames": 0,
      "heartbeats_received": 0,
      "heartbeats_sent": 0,
      "commands_sent": 0,
      "stop_commands_sent": 0,
      "deadman_stops": 0,
  }

  print(f"recording={output_dir}")

  with ControlClient(args, output_dir, summary) as control:
    control_thread = threading.Thread(target=control.run, daemon=True)
    video_thread = threading.Thread(target=video_worker, args=(args, output_dir, summary), daemon=True)
    control_thread.start()
    video_thread.start()
    control.connected.wait(timeout=6)
    teleop_loop(args, control, summary)
    stop_event.set()
    video_thread.join(timeout=5)
    control_thread.join(timeout=5)

  summary["ended_monotonic_ns"] = time.monotonic_ns()
  elapsed_s = (
      int(summary["ended_monotonic_ns"]) - int(summary["started_monotonic_ns"])
  ) / 1_000_000_000
  summary["elapsed_s"] = f"{elapsed_s:.2f}"
  (output_dir / "summary.json").write_text(
      json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
  )
  print(
      "summary "
      f"frames={summary.get('frames', 0)} "
      f"heartbeats={summary.get('heartbeats_received', 0)} "
      f"commands={summary.get('commands_sent', 0)} "
      f"elapsed_s={summary['elapsed_s']}"
  )
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
