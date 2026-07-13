#!/usr/bin/env python3
"""Browser teleop and recording app for the ELEGOO Conqueror robot.

The app proxies the ESP32 MJPEG stream to the browser and records the exact
JPEG frames it forwards. It also keeps the ESP32 control socket alive by
replying to `{Heartbeat}` messages.

Drive commands are disabled at startup and must be armed from the browser UI.
Use `--lock-drive` when the UI must not be allowed to arm driving.
"""

from __future__ import annotations

import argparse
import atexit
import csv
import json
import signal
import socket
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import BinaryIO
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from video_probe import DEFAULT_STATUS_URL, DEFAULT_STREAM_URL, fetch_status, jpeg_dimensions, utc_stamp


DEFAULT_CONTROL_HOST = "192.168.4.1"
DEFAULT_CONTROL_PORT = 100
DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_BIND_PORT = 8765

HEARTBEAT = b"{Heartbeat}"
STOP_COMMAND = {"N": 102, "D1": 9, "D2": 0}
STOCK_CLEAR_COMMAND = {"N": 100}
SOI = b"\xff\xd8"
EOI = b"\xff\xd9"
BOUNDARY = "elegoo-stream-boundary"

DRIVE_COMMANDS = {
    "forward": {"N": 102, "D1": 1, "D2": None},
    "backward": {"N": 102, "D1": 2, "D2": None},
    "left": {"N": 102, "D1": 3, "D2": None},
    "right": {"N": 102, "D1": 4, "D2": None},
    "left_forward": {"N": 102, "D1": 5, "D2": None},
    "left_backward": {"N": 102, "D1": 6, "D2": None},
    "right_forward": {"N": 102, "D1": 7, "D2": None},
    "right_backward": {"N": 102, "D1": 8, "D2": None},
    "stop": STOP_COMMAND,
}

MODE_COMMANDS = {
    "standby": STOCK_CLEAR_COMMAND,
    "line_tracking": {"N": 101, "D1": 1},
    "obstacle_avoidance": {"N": 101, "D1": 2},
    "follow": {"N": 101, "D1": 3},
}


@dataclass
class AppState:
  output_dir: Path
  frames_dir: Path
  frames_csv: object
  frames_writer: csv.DictWriter
  control_csv: object
  control_writer: csv.DictWriter
  started_monotonic_ns: int
  video_url: str
  status_url: str
  control_host: str
  control_port: int
  drive_allowed: bool
  drive_enabled: bool
  speed: int
  deadman_ms: int
  mode: str = "manual"
  pitch: int = 90
  yaw: int = 90
  frames: int = 0
  heartbeats_received: int = 0
  heartbeats_sent: int = 0
  commands_sent: int = 0
  camera_commands_sent: int = 0
  ignored_drive_commands: int = 0
  deadman_stops: int = 0
  stop_commands_sent: int = 0
  control_status: str = "starting"
  video_clients: int = 0
  last_drive_monotonic_ns: int = 0
  last_frame_width: int = 0
  last_frame_height: int = 0
  last_error: str = ""


class ControlClient:
  def __init__(self, state: AppState):
    self.state = state
    self.sock: socket.socket | None = None
    self.lock = threading.Lock()
    self.stop_event = threading.Event()
    self.thread = threading.Thread(target=self._run, daemon=True)
    self.deadman_thread = threading.Thread(target=self._deadman_loop, daemon=True)

  def start(self) -> None:
    self.thread.start()
    self.deadman_thread.start()

  def stop(self) -> None:
    self.stop_event.set()
    if self.state.drive_enabled:
      self.send_drive("stop", reason="shutdown")
    with self.lock:
      if self.sock is not None:
        try:
          self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
          pass
        try:
          self.sock.close()
        except OSError:
          pass
        self.sock = None

  def _log(self, event: str, payload: str = "") -> None:
    self.state.control_writer.writerow({
        "monotonic_ns": time.monotonic_ns(),
        "event": event,
        "payload": payload,
    })
    self.state.control_csv.flush()

  def _send_text(self, payload: str, event: str) -> bool:
    with self.lock:
      if self.sock is None:
        self._log("send_failed", payload)
        return False
      try:
        self.sock.sendall(payload.encode("utf-8"))
      except OSError as exc:
        self.state.last_error = str(exc)
        self._log("send_error", str(exc))
        return False
    self._log(event, payload)
    return True

  def _run(self) -> None:
    while not self.stop_event.is_set():
      try:
        with socket.create_connection((self.state.control_host, self.state.control_port), timeout=5) as sock:
          sock.settimeout(1)
          with self.lock:
            self.sock = sock
          self.state.control_status = "connected"
          self._log("connected", f"{self.state.control_host}:{self.state.control_port}")
          self._read_loop(sock)
      except OSError as exc:
        if self.stop_event.is_set():
          break
        self.state.control_status = "error"
        self.state.last_error = str(exc)
        self._log("control_error", str(exc))
        time.sleep(1)
      finally:
        with self.lock:
          self.sock = None
        if not self.stop_event.is_set():
          self.state.control_status = "reconnecting"

  def _read_loop(self, sock: socket.socket) -> None:
    buffer = bytearray()
    while not self.stop_event.is_set():
      try:
        data = sock.recv(1024)
      except socket.timeout:
        continue
      if not data:
        self._log("disconnected_by_peer")
        break
      buffer.extend(data)
      self._process_buffer(buffer)

  def _process_buffer(self, buffer: bytearray) -> None:
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
      self._log("recv", text)
      if message == HEARTBEAT:
        if self._send_text(HEARTBEAT.decode("ascii"), "sent_heartbeat"):
          self.state.heartbeats_received += 1
          self.state.heartbeats_sent += 1

  def send_drive(self, action: str, reason: str = "ui") -> bool:
    command = DRIVE_COMMANDS[action].copy()
    if command.get("D2") is None:
      command["D2"] = self.state.speed
    payload = json.dumps(command, separators=(",", ":"))
    if not self.state.drive_allowed or not self.state.drive_enabled:
      self.state.ignored_drive_commands += 1
      self._log("drive_ignored", f"{reason}:{action}:{payload}")
      return False
    if self._send_text(payload, "sent_command"):
      self.state.commands_sent += 1
      if action == "stop":
        self.state.last_drive_monotonic_ns = 0
        self.state.stop_commands_sent += 1
      else:
        self.state.last_drive_monotonic_ns = time.monotonic_ns()
        self.state.mode = "manual"
      return True
    return False

  def set_drive_enabled(self, enabled: bool) -> bool:
    if enabled and not self.state.drive_allowed:
      self._log("drive_enable_denied", "server_not_started_with_enable_drive")
      return False
    if not enabled and self.state.drive_enabled:
      self.send_drive("stop", reason="drive_disabled")
    self.state.drive_enabled = enabled
    self.state.last_drive_monotonic_ns = 0
    self._log("drive_enabled", str(enabled).lower())
    return True

  def set_mode(self, mode: str) -> bool:
    if mode == "manual":
      self.state.mode = "manual"
      self.send_stock_clear()
      self._log("mode_changed", "manual")
      return True
    command = MODE_COMMANDS.get(mode)
    if command is None:
      return False
    if mode != "standby" and (not self.state.drive_allowed or not self.state.drive_enabled):
      self._log("mode_ignored", f"drive_disarmed:{mode}")
      return False
    payload = json.dumps(command, separators=(",", ":"))
    if self._send_text(payload, "sent_mode"):
      self.state.mode = mode
      if mode == "standby":
        self.state.last_drive_monotonic_ns = 0
        self.state.stop_commands_sent += 1
      return True
    return False

  def send_stock_clear(self) -> bool:
    payload = json.dumps(STOCK_CLEAR_COMMAND, separators=(",", ":"))
    if self._send_text(payload, "sent_stock_clear"):
      self.state.stop_commands_sent += 1
      return True
    return False

  def send_camera_angle(self, axis: str, angle: int) -> bool:
    servo = 2 if axis == "pitch" else 1
    payload = json.dumps({"N": 5, "D1": servo, "D2": angle}, separators=(",", ":"))
    if self._send_text(payload, "sent_camera"):
      self.state.camera_commands_sent += 1
      return True
    return False

  def _deadman_loop(self) -> None:
    while not self.stop_event.is_set():
      time.sleep(0.05)
      if not self.state.drive_enabled:
        continue
      last = self.state.last_drive_monotonic_ns
      if not last:
        continue
      age_ms = (time.monotonic_ns() - last) / 1_000_000
      if age_ms > self.state.deadman_ms:
        if self.send_drive("stop", reason="deadman"):
          self.state.deadman_stops += 1


class WebApp:
  def __init__(self, args: argparse.Namespace):
    self.args = args
    self.stop_event = threading.Event()
    status = fetch_status(args.status_url)
    output_dir = args.output_dir or Path("recordings") / utc_stamp()
    frames_dir = output_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    frames_csv = (output_dir / "frames.csv").open("w", newline="", encoding="utf-8")
    frames_writer = csv.DictWriter(
        frames_csv,
        fieldnames=["frame_id", "monotonic_ns", "filename", "width", "height", "byte_count", "decode_ok"],
    )
    frames_writer.writeheader()

    control_csv = (output_dir / "control_events.csv").open("w", newline="", encoding="utf-8")
    control_writer = csv.DictWriter(control_csv, fieldnames=["monotonic_ns", "event", "payload"])
    control_writer.writeheader()

    self.state = AppState(
        output_dir=output_dir,
        frames_dir=frames_dir,
        frames_csv=frames_csv,
        frames_writer=frames_writer,
        control_csv=control_csv,
        control_writer=control_writer,
        started_monotonic_ns=time.monotonic_ns(),
        video_url=args.video_url,
        status_url=args.status_url,
        control_host=args.control_host,
        control_port=args.control_port,
        drive_allowed=not args.lock_drive,
        drive_enabled=False,
        speed=clamp(args.speed, 0, 255),
        deadman_ms=max(100, args.deadman_ms),
    )
    self.control = ControlClient(self.state)
    self.server: ThreadingHTTPServer | None = None
    self._write_metadata(status)
    atexit.register(self.shutdown)

  def _write_metadata(self, status: dict[str, object] | None) -> None:
    metadata = {
        "created_utc": utc_stamp(),
        "app": "web_teleop_app",
        "video_url": self.state.video_url,
        "status_url": self.state.status_url,
        "control_host": self.state.control_host,
        "control_port": self.state.control_port,
        "drive_allowed": self.state.drive_allowed,
        "drive_enabled": self.state.drive_enabled,
        "drive_speed": self.state.speed,
        "deadman_ms": self.state.deadman_ms,
        "camera_pitch_initial": self.state.pitch,
        "camera_yaw_initial": self.state.yaw,
        "camera_status": status,
    }
    (self.state.output_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

  def start(self) -> None:
    self.control.start()
    handler = make_handler(self)
    self.server = ThreadingHTTPServer((self.args.bind_host, self.args.bind_port), handler)
    self.server.timeout = 1
    url = f"http://{self.args.bind_host}:{self.server.server_port}/"
    print(f"recording={self.state.output_dir}")
    print(f"app={url}")
    print(f"drive_allowed={self.state.drive_allowed}")
    if self.args.open_browser:
      webbrowser.open(url)

    deadline = time.monotonic() + self.args.duration if self.args.duration > 0 else None
    while not self.stop_event.is_set():
      if deadline is not None and time.monotonic() >= deadline:
        break
      self.server.handle_request()
    self.shutdown()

  def shutdown(self) -> None:
    if self.stop_event.is_set():
      return
    self.stop_event.set()
    self.control.stop()
    if self.server is not None:
      try:
        self.server.server_close()
      except OSError:
        pass
    self._write_summary()
    try:
      self.state.frames_csv.close()
      self.state.control_csv.close()
    except Exception:
      pass

  def _write_summary(self) -> None:
    ended = time.monotonic_ns()
    summary = {
        "started_monotonic_ns": self.state.started_monotonic_ns,
        "ended_monotonic_ns": ended,
        "elapsed_s": f"{(ended - self.state.started_monotonic_ns) / 1_000_000_000:.2f}",
        "frames": self.state.frames,
        "drive_allowed": self.state.drive_allowed,
        "drive_enabled": self.state.drive_enabled,
        "mode": self.state.mode,
        "last_width": self.state.last_frame_width,
        "last_height": self.state.last_frame_height,
        "heartbeats_received": self.state.heartbeats_received,
        "heartbeats_sent": self.state.heartbeats_sent,
        "commands_sent": self.state.commands_sent,
        "camera_commands_sent": self.state.camera_commands_sent,
        "ignored_drive_commands": self.state.ignored_drive_commands,
        "deadman_stops": self.state.deadman_stops,
        "stop_commands_sent": self.state.stop_commands_sent,
        "control_status": self.state.control_status,
        "last_error": self.state.last_error,
    }
    (self.state.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def clamp(value: int, low: int, high: int) -> int:
  return max(low, min(high, value))


def iter_jpegs(response: BinaryIO, chunk_size: int, stop_event: threading.Event):
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


def make_handler(app: WebApp):
  class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
      if app.args.verbose:
        super().log_message(fmt, *args)

    def do_GET(self) -> None:
      parsed = urlparse(self.path)
      if parsed.path == "/":
        self._send_html()
      elif parsed.path == "/stream":
        self._stream()
      elif parsed.path == "/api/state":
        self._send_json(current_state(app.state))
      else:
        self.send_error(404)

    def do_POST(self) -> None:
      parsed = urlparse(self.path)
      length = int(self.headers.get("Content-Length", "0"))
      body = self.rfile.read(length) if length else b"{}"
      try:
        data = json.loads(body.decode("utf-8"))
      except json.JSONDecodeError:
        self.send_error(400, "bad json")
        return

      if parsed.path == "/api/drive":
        action = str(data.get("action", "stop"))
        if action not in DRIVE_COMMANDS:
          self.send_error(400, "bad drive action")
          return
        sent = app.control.send_drive(action)
        self._send_json({"ok": True, "sent": sent, **current_state(app.state)})
      elif parsed.path == "/api/drive_enabled":
        enabled = bool(data.get("enabled", False))
        ok = app.control.set_drive_enabled(enabled)
        self._send_json({"ok": ok, **current_state(app.state)})
      elif parsed.path == "/api/mode":
        mode = str(data.get("mode", "manual"))
        ok = app.control.set_mode(mode)
        self._send_json({"ok": ok, **current_state(app.state)})
      elif parsed.path == "/api/speed":
        delta = int(data.get("delta", 0))
        if "value" in data:
          app.state.speed = clamp(int(data["value"]), 0, 255)
        else:
          app.state.speed = clamp(app.state.speed + delta, 0, 255)
        app.control._log("speed_changed", str(app.state.speed))
        self._send_json({"ok": True, **current_state(app.state)})
      elif parsed.path == "/api/camera":
        axis = str(data.get("axis", ""))
        delta = int(data.get("delta", 0))
        if axis == "pitch":
          app.state.pitch = clamp(app.state.pitch + delta, 30, 110)
          sent = app.control.send_camera_angle("pitch", app.state.pitch)
        elif axis == "yaw":
          app.state.yaw = clamp(app.state.yaw + delta, 0, 180)
          sent = app.control.send_camera_angle("yaw", app.state.yaw)
        else:
          self.send_error(400, "bad camera axis")
          return
        self._send_json({"ok": True, "sent": sent, **current_state(app.state)})
      elif parsed.path == "/api/center_camera":
        app.state.pitch = 90
        app.state.yaw = 90
        pitch_sent = app.control.send_camera_angle("pitch", app.state.pitch)
        yaw_sent = app.control.send_camera_angle("yaw", app.state.yaw)
        self._send_json({"ok": True, "sent": pitch_sent and yaw_sent, **current_state(app.state)})
      elif parsed.path == "/api/shutdown":
        self._send_json({"ok": True})
        threading.Thread(target=app.shutdown, daemon=True).start()
      else:
        self.send_error(404)

    def _send_html(self) -> None:
      body = INDEX_HTML.encode("utf-8")
      self.send_response(200)
      self.send_header("Content-Type", "text/html; charset=utf-8")
      self.send_header("Content-Length", str(len(body)))
      self.end_headers()
      self.wfile.write(body)

    def _send_json(self, payload: dict[str, object]) -> None:
      body = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
      self.send_response(200)
      self.send_header("Content-Type", "application/json")
      self.send_header("Content-Length", str(len(body)))
      self.end_headers()
      self.wfile.write(body)

    def _stream(self) -> None:
      app.state.video_clients += 1
      self.send_response(200)
      self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
      self.send_header("Cache-Control", "no-store")
      self.end_headers()

      try:
        request = Request(app.state.video_url, headers={"User-Agent": "elegoo-web-teleop/0.1"})
        with urlopen(request, timeout=5) as response:
          for frame in iter_jpegs(response, app.args.chunk_size, app.stop_event):
            if app.stop_event.is_set():
              break
            frame_id = app.state.frames
            width, height = jpeg_dimensions(frame)
            filename = f"frames/{frame_id:06d}.jpg"
            (app.state.frames_dir / f"{frame_id:06d}.jpg").write_bytes(frame)
            now_ns = time.monotonic_ns()
            app.state.frames_writer.writerow({
                "frame_id": frame_id,
                "monotonic_ns": now_ns,
                "filename": filename,
                "width": width if width is not None else "",
                "height": height if height is not None else "",
                "byte_count": len(frame),
                "decode_ok": str(width is not None and height is not None).lower(),
            })
            app.state.frames_csv.flush()
            app.state.frames += 1
            app.state.last_frame_width = width or 0
            app.state.last_frame_height = height or 0

            header = (
                f"--{BOUNDARY}\r\n"
                "Content-Type: image/jpeg\r\n"
                f"Content-Length: {len(frame)}\r\n"
                f"X-Frame-Id: {frame_id}\r\n"
                "\r\n"
            ).encode("ascii")
            self.wfile.write(header)
            self.wfile.write(frame)
            self.wfile.write(b"\r\n")
            self.wfile.flush()
      except (BrokenPipeError, ConnectionResetError, ValueError):
        pass
      except URLError as exc:
        app.state.last_error = str(exc)
      finally:
        app.state.video_clients -= 1

  return Handler


def current_state(state: AppState) -> dict[str, object]:
  return {
      "recording": str(state.output_dir),
      "drive_allowed": state.drive_allowed,
      "drive_enabled": state.drive_enabled,
      "mode": state.mode,
      "speed": state.speed,
      "deadman_ms": state.deadman_ms,
      "pitch": state.pitch,
      "yaw": state.yaw,
      "frames": state.frames,
      "video_clients": state.video_clients,
      "control_status": state.control_status,
      "heartbeats_received": state.heartbeats_received,
      "heartbeats_sent": state.heartbeats_sent,
      "commands_sent": state.commands_sent,
      "camera_commands_sent": state.camera_commands_sent,
      "ignored_drive_commands": state.ignored_drive_commands,
      "deadman_stops": state.deadman_stops,
      "last_error": state.last_error,
  }


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ELEGOO Robot Teleop</title>
  <style>
    :root { color-scheme: dark; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; background: #101316; color: #f4f5f5; }
    main { display: grid; grid-template-columns: minmax(0, 1fr) 300px; height: 100vh; overflow: hidden; }
    .video { background: #050607; display: grid; place-items: center; overflow: hidden; }
    .video img { max-width: 100%; max-height: 100vh; width: auto; height: auto; display: block; }
    aside { border-left: 1px solid #2a3036; background: #171b20; display: flex; flex-direction: column; min-height: 0; }
    .panel { padding: 12px; overflow-y: auto; min-height: 0; }
    .footer { border-top: 1px solid #2a3036; padding: 10px 12px; background: #14181d; }
    h1 { font-size: 16px; margin: 0 0 8px; }
    h2 { font-size: 11px; margin: 12px 0 6px; color: #aeb7c2; text-transform: uppercase; }
    .metrics { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
    .metric { border: 1px solid #252c34; background: #1d232a; border-radius: 6px; padding: 6px 7px; min-width: 0; }
    .metric span:first-child { display: block; color: #88929d; font-size: 10px; line-height: 1.1; }
    .metric span:last-child { display: block; color: #dce3ea; font-size: 12px; line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .metric.wide { grid-column: 1 / -1; }
    .keys { display: grid; grid-template-columns: repeat(3, 1fr); gap: 5px; margin-top: 6px; }
    .key { border: 1px solid #38414a; background: #20262d; padding: 6px 4px; text-align: center; border-radius: 5px; font-size: 11px; min-height: 16px; }
    .buttons { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-top: 6px; }
    .buttons.three { grid-template-columns: repeat(3, 1fr); }
    .buttons button { margin-top: 0; }
    .warn { color: #ffd37a; }
    .ok { color: #91e6a8; }
    .bad { color: #ff8a8a; }
    button { width: 100%; padding: 7px 8px; border-radius: 6px; border: 1px solid #46515d; background: #242b33; color: #fff; font: inherit; font-size: 12px; }
    button.primary { background: #28415d; border-color: #4d6f93; }
    button.danger { background: #4a2529; border-color: #7c3c45; }
    button:disabled { opacity: 0.45; }
    #message { min-height: 16px; margin: 8px 0 0; font-size: 11px; overflow-wrap: anywhere; }
    @media (max-width: 900px) { main { grid-template-columns: 1fr; grid-template-rows: minmax(0, 1fr) auto; } aside { border-left: 0; border-top: 1px solid #2a3036; max-height: 48vh; } }
  </style>
</head>
<body>
  <main>
    <section class="video">
      <img src="/stream" alt="Robot camera stream">
    </section>
    <aside>
      <div class="panel">
        <h1>ELEGOO Teleop</h1>
        <div class="metrics">
          <div class="metric"><span>Drive</span><span id="drive" class="warn">checking</span></div>
          <div class="metric"><span>Control</span><span id="control">checking</span></div>
          <div class="metric"><span>Mode</span><span id="mode">manual</span></div>
          <div class="metric"><span>Frames</span><span id="frames">0</span></div>
          <div class="metric"><span>Speed</span><span id="speed">0</span></div>
          <div class="metric"><span>Camera</span><span><span id="pitch">90</span>/<span id="yaw">90</span></span></div>
          <div class="metric wide"><span>Recording</span><span id="recording"></span></div>
        </div>

        <h2>Drive</h2>
        <div class="keys">
          <div></div><div class="key">W / ↑</div><div></div>
          <div class="key">A / ←</div><div class="key">Space</div><div class="key">D / →</div>
          <div></div><div class="key">S / ↓</div><div></div>
        </div>
        <div class="keys">
          <div class="key">- speed</div><div class="key">X stop</div><div class="key">+ speed</div>
        </div>

        <h2>Modes</h2>
        <div class="buttons three">
          <button onclick="setMode('manual')">Manual</button>
          <button onclick="setMode('standby')">Standby</button>
          <button onclick="setMode('line_tracking')">Line</button>
          <button onclick="setMode('obstacle_avoidance')">Obstacle</button>
          <button onclick="setMode('follow')">Follow</button>
        </div>

        <h2>Camera</h2>
        <div class="keys">
          <div></div><div class="key">I pitch up</div><div></div>
          <div class="key">J yaw left</div><div class="key">C center</div><div class="key">L yaw right</div>
          <div></div><div class="key">K pitch down</div><div></div>
        </div>
        <div class="buttons">
          <button onclick="centerCamera()">Center Camera</button>
        </div>
        <p id="message" class="warn"></p>
      </div>
      <div class="footer">
        <div class="buttons">
          <button id="driveToggle" class="primary" onclick="toggleDrive()">Arm Drive</button>
          <button class="danger" onclick="drive('stop')">Stop</button>
        </div>
        <div class="buttons">
          <button onclick="shutdown()">Stop Server</button>
        </div>
      </div>
    </aside>
  </main>
  <script>
    const active = new Set();
    const driveMap = {
      "w": "forward", "arrowup": "forward",
      "s": "backward", "arrowdown": "backward",
      "a": "left", "arrowleft": "left",
      "d": "right", "arrowright": "right",
      "x": "stop", " ": "stop"
    };

    async function post(path, data = {}) {
      const res = await fetch(path, { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data) });
      return await res.json();
    }
    async function drive(action) {
      const state = await post("/api/drive", {action});
      update(state);
    }
    async function speed(delta) {
      const state = await post("/api/speed", {delta});
      update(state);
    }
    async function toggleDrive() {
      const enabled = document.getElementById("drive").dataset.enabled !== "true";
      if (enabled) {
        const ok = window.confirm("Enable robot driving?\n\nBefore continuing, make sure the robot is on the ground in a clear area or safely lifted for bench testing. Keep access to the power switch.");
        if (!ok) return;
      }
      const state = await post("/api/drive_enabled", {enabled});
      update(state);
    }
    async function setMode(mode) {
      const state = await post("/api/mode", {mode});
      update(state);
    }
    async function camera(axis, delta) {
      const state = await post("/api/camera", {axis, delta});
      update(state);
    }
    async function centerCamera() {
      const state = await post("/api/center_camera", {});
      update(state);
    }
    async function shutdown() {
      await post("/api/shutdown", {});
      document.getElementById("message").textContent = "Server shutdown requested.";
    }
    function update(state) {
      const driveEl = document.getElementById("drive");
      driveEl.textContent = state.drive_enabled ? "armed" : "disarmed";
      driveEl.className = state.drive_enabled ? "ok" : "warn";
      driveEl.dataset.enabled = state.drive_enabled ? "true" : "false";
      document.getElementById("mode").textContent = state.mode;
      document.getElementById("control").textContent = state.control_status;
      document.getElementById("control").className = state.control_status === "connected" ? "ok" : "bad";
      document.getElementById("driveToggle").textContent = state.drive_enabled ? "Disarm Drive" : "Arm Drive";
      document.getElementById("driveToggle").disabled = !state.drive_allowed;
      document.getElementById("frames").textContent = state.frames;
      document.getElementById("speed").textContent = state.speed;
      document.getElementById("pitch").textContent = state.pitch;
      document.getElementById("yaw").textContent = state.yaw;
      document.getElementById("recording").textContent = state.recording;
      document.getElementById("message").textContent = state.last_error || "";
    }
    document.addEventListener("keydown", (event) => {
      const key = event.key.toLowerCase();
      if (["arrowup","arrowdown","arrowleft","arrowright"," "].includes(key)) event.preventDefault();
      if (active.has(key) && key !== " " && key !== "x") return;
      active.add(key);
      if (driveMap[key]) drive(driveMap[key]);
      else if (key === "+" || key === "=") speed(10);
      else if (key === "-" || key === "_") speed(-10);
      else if (key === "i") camera("pitch", -5);
      else if (key === "k") camera("pitch", 5);
      else if (key === "j") camera("yaw", 5);
      else if (key === "l") camera("yaw", -5);
      else if (key === "c") centerCamera();
    });
    document.addEventListener("keyup", (event) => {
      const key = event.key.toLowerCase();
      active.delete(key);
      if (["w","a","s","d","arrowup","arrowdown","arrowleft","arrowright"].includes(key)) drive("stop");
    });
    setInterval(async () => {
      try {
        const res = await fetch("/api/state");
        update(await res.json());
      } catch (_) {}
    }, 500);
  </script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="Run the local browser teleop app.")
  parser.add_argument("--video-url", default=DEFAULT_STREAM_URL)
  parser.add_argument("--status-url", default=DEFAULT_STATUS_URL)
  parser.add_argument("--control-host", default=DEFAULT_CONTROL_HOST)
  parser.add_argument("--control-port", type=int, default=DEFAULT_CONTROL_PORT)
  parser.add_argument("--bind-host", default=DEFAULT_BIND_HOST)
  parser.add_argument("--bind-port", type=int, default=DEFAULT_BIND_PORT)
  parser.add_argument("--output-dir", type=Path, default=None)
  parser.add_argument("--speed", type=int, default=80)
  parser.add_argument("--deadman-ms", type=int, default=500)
  parser.add_argument(
      "--lock-drive",
      action="store_true",
      help="Prevent the browser UI from arming drive commands.",
  )
  parser.add_argument("--duration", type=float, default=0.0, help="Optional auto-shutdown timeout for testing.")
  parser.add_argument("--chunk-size", type=int, default=8192)
  parser.add_argument("--no-open", dest="open_browser", action="store_false")
  parser.add_argument("--verbose", action="store_true")
  parser.set_defaults(open_browser=True)
  return parser.parse_args()


def main() -> int:
  args = parse_args()
  app = WebApp(args)

  def _signal(signum: int, frame: object) -> None:
    del signum, frame
    app.shutdown()

  signal.signal(signal.SIGINT, _signal)
  signal.signal(signal.SIGTERM, _signal)
  app.start()
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
