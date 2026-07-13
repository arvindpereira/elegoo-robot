#!/usr/bin/env python3
"""Record synchronized video frames and ESP32 control-heartbeat events.

Default behavior is no-drive: the control socket only replies to `{Heartbeat}`.
It does not send movement commands. The optional `--send-stop` flag sends the
stock stop/clear command on connect and shutdown.
"""

from __future__ import annotations

import argparse
import csv
import json
import signal
import socket
import threading
import time
from pathlib import Path
from typing import BinaryIO
from urllib.error import URLError
from urllib.request import Request, urlopen

from video_probe import DEFAULT_STATUS_URL, DEFAULT_STREAM_URL, fetch_status, jpeg_dimensions, utc_stamp


DEFAULT_CONTROL_HOST = "192.168.4.1"
DEFAULT_CONTROL_PORT = 100
HEARTBEAT = b"{Heartbeat}"
STOP_COMMAND = b'{"N":100}'
SOI = b"\xff\xd8"
EOI = b"\xff\xd9"


stop_event = threading.Event()


def handle_stop(signum: int, frame: object) -> None:
  del signum, frame
  stop_event.set()


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(
      description="Record ESP32 camera frames and heartbeat/control events."
  )
  parser.add_argument("--video-url", default=DEFAULT_STREAM_URL, help="MJPEG stream URL.")
  parser.add_argument("--status-url", default=DEFAULT_STATUS_URL, help="Camera status URL.")
  parser.add_argument("--control-host", default=DEFAULT_CONTROL_HOST, help="ESP32 control host.")
  parser.add_argument("--control-port", type=int, default=DEFAULT_CONTROL_PORT, help="ESP32 control port.")
  parser.add_argument(
      "--duration",
      type=float,
      default=10.0,
      help="Seconds to record. Use 0 to run until interrupted.",
  )
  parser.add_argument(
      "--output-dir",
      type=Path,
      default=None,
      help="Recording directory. Defaults to recordings/<UTC timestamp>.",
  )
  parser.add_argument(
      "--no-save-frames",
      action="store_true",
      help="Record frame metadata without writing JPEG files.",
  )
  parser.add_argument(
      "--no-control",
      action="store_true",
      help="Skip the ESP32 control socket and record video only.",
  )
  parser.add_argument(
      "--send-stop",
      action="store_true",
      help="Send {\"N\":100} on control connect and shutdown. No movement commands are sent.",
  )
  parser.add_argument("--chunk-size", type=int, default=8192, help="HTTP read chunk size.")
  parser.add_argument("--connect-timeout", type=float, default=5.0, help="Control socket connect timeout.")
  parser.add_argument("--read-timeout", type=float, default=1.0, help="Control socket read timeout.")
  parser.add_argument("--report-every", type=int, default=30, help="Print progress every N frames.")
  return parser.parse_args()


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
      "save_frames": not args.no_save_frames,
      "control_enabled": not args.no_control,
      "send_stop": args.send_stop,
      "camera_status": status,
  }
  (output_dir / "metadata.json").write_text(
      json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
  )
  return output_dir


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


def write_control_event(writer: csv.DictWriter, event: str, payload: str = "") -> None:
  writer.writerow({
      "monotonic_ns": time.monotonic_ns(),
      "event": event,
      "payload": payload,
  })


def control_worker(args: argparse.Namespace, output_dir: Path, summary: dict[str, int | str]) -> None:
  control_csv = output_dir / "control_events.csv"
  with control_csv.open("w", newline="", encoding="utf-8") as csv_file:
    writer = csv.DictWriter(csv_file, fieldnames=["monotonic_ns", "event", "payload"])
    writer.writeheader()

    if args.no_control:
      write_control_event(writer, "control_skipped")
      summary["control_status"] = "skipped"
      return

    try:
      with socket.create_connection(
          (args.control_host, args.control_port),
          timeout=args.connect_timeout,
      ) as sock:
        sock.settimeout(args.read_timeout)
        write_control_event(writer, "connected", f"{args.control_host}:{args.control_port}")
        summary["control_status"] = "connected"

        if args.send_stop:
          sock.sendall(STOP_COMMAND)
          write_control_event(writer, "sent_stop", STOP_COMMAND.decode("ascii"))
          summary["stop_commands_sent"] = int(summary.get("stop_commands_sent", 0)) + 1

        buffer = bytearray()
        while not stop_event.is_set():
          try:
            data = sock.recv(1024)
          except socket.timeout:
            continue

          if not data:
            write_control_event(writer, "disconnected_by_peer")
            break

          summary["control_bytes_received"] = int(summary.get("control_bytes_received", 0)) + len(data)
          buffer.extend(data)

          while True:
            start = buffer.find(b"{")
            if start < 0:
              buffer.clear()
              break
            if start > 0:
              del buffer[:start]
            end = buffer.find(b"}", 1)
            if end < 0:
              break

            message = bytes(buffer[:end + 1])
            del buffer[:end + 1]
            text = message.decode("utf-8", "replace")
            write_control_event(writer, "recv", text)

            if message == HEARTBEAT:
              sock.sendall(HEARTBEAT)
              write_control_event(writer, "sent_heartbeat", HEARTBEAT.decode("ascii"))
              summary["heartbeats_received"] = int(summary.get("heartbeats_received", 0)) + 1
              summary["heartbeats_sent"] = int(summary.get("heartbeats_sent", 0)) + 1

        if args.send_stop:
          sock.sendall(STOP_COMMAND)
          write_control_event(writer, "sent_stop", STOP_COMMAND.decode("ascii"))
          summary["stop_commands_sent"] = int(summary.get("stop_commands_sent", 0)) + 1

    except OSError as exc:
      write_control_event(writer, "control_error", str(exc))
      summary["control_status"] = "error"
      summary["control_error"] = str(exc)


def video_worker(args: argparse.Namespace, output_dir: Path, summary: dict[str, int | str]) -> None:
  frames_csv = output_dir / "frames.csv"
  frames_dir = output_dir / "frames"

  with frames_csv.open("w", newline="", encoding="utf-8") as csv_file:
    writer = csv.DictWriter(
        csv_file,
        fieldnames=[
            "frame_id",
            "monotonic_ns",
            "filename",
            "width",
            "height",
            "byte_count",
            "decode_ok",
        ],
    )
    writer.writeheader()

    try:
      request = Request(args.video_url, headers={"User-Agent": "elegoo-session-recorder/0.1"})
      with urlopen(request, timeout=5) as response:
        summary["video_content_type"] = response.headers.get("Content-Type", "")
        print(f"video_content_type={summary['video_content_type']}")

        for frame_id, frame in enumerate(iter_jpegs(response, args.chunk_size)):
          width, height = jpeg_dimensions(frame)
          decode_ok = width is not None and height is not None
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
              "decode_ok": str(decode_ok).lower(),
          })

          summary["frames"] = frame_id + 1
          summary["last_width"] = width or ""
          summary["last_height"] = height or ""
          if args.report_every > 0 and (frame_id + 1) % args.report_every == 0:
            print(f"frames={frame_id + 1} last={width}x{height} bytes={len(frame)}")

    except URLError as exc:
      summary["video_status"] = "error"
      summary["video_error"] = str(exc)
      stop_event.set()
      return

  summary["video_status"] = "ok"


def main() -> int:
  signal.signal(signal.SIGINT, handle_stop)
  signal.signal(signal.SIGTERM, handle_stop)

  args = parse_args()
  status = fetch_status(args.status_url)
  output_dir = prepare_recording(args, status)
  print(f"recording={output_dir}")

  summary: dict[str, int | str] = {
      "frames": 0,
      "heartbeats_received": 0,
      "heartbeats_sent": 0,
      "stop_commands_sent": 0,
      "started_monotonic_ns": time.monotonic_ns(),
  }

  video_thread = threading.Thread(target=video_worker, args=(args, output_dir, summary), daemon=True)
  control_thread = threading.Thread(target=control_worker, args=(args, output_dir, summary), daemon=True)
  video_thread.start()
  control_thread.start()

  deadline = None
  if args.duration > 0:
    deadline = time.monotonic() + args.duration

  try:
    while not stop_event.is_set():
      if deadline is not None and time.monotonic() >= deadline:
        stop_event.set()
        break
      if not video_thread.is_alive():
        stop_event.set()
        break
      time.sleep(0.1)
  finally:
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
      f"stops={summary.get('stop_commands_sent', 0)} "
      f"elapsed_s={summary['elapsed_s']}"
  )

  return 0 if int(summary.get("frames", 0)) > 0 else 1


if __name__ == "__main__":
  raise SystemExit(main())

