#!/usr/bin/env python3
"""Probe and record the ELEGOO ESP32 MJPEG camera stream.

This tool intentionally does not open the robot control socket or send motor
commands. It only reads video from the ESP32 camera HTTP stream.
"""

from __future__ import annotations

import argparse
import csv
import json
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO
from urllib.error import URLError
from urllib.request import Request, urlopen


DEFAULT_STREAM_URL = "http://192.168.4.1:81/stream"
DEFAULT_STATUS_URL = "http://192.168.4.1/status"
SOI = b"\xff\xd8"
EOI = b"\xff\xd9"


stop_requested = False


@dataclass
class FrameStats:
  frame_id: int
  monotonic_ns: int
  filename: str
  width: int | None
  height: int | None
  byte_count: int
  decode_ok: bool


def handle_stop(signum: int, frame: object) -> None:
  del signum, frame
  global stop_requested
  stop_requested = True


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(
      description="Read and optionally record the ELEGOO ESP32 MJPEG stream."
  )
  parser.add_argument("--url", default=DEFAULT_STREAM_URL, help="MJPEG stream URL.")
  parser.add_argument(
      "--status-url", default=DEFAULT_STATUS_URL, help="Camera status endpoint."
  )
  parser.add_argument(
      "--duration",
      type=float,
      default=10.0,
      help="Seconds to run. Use 0 to run until interrupted.",
  )
  parser.add_argument(
      "--max-frames",
      type=int,
      default=0,
      help="Maximum frames to read. Use 0 for no explicit frame limit.",
  )
  parser.add_argument(
      "--output-dir",
      type=Path,
      default=None,
      help="Recording directory. Defaults to recordings/<UTC timestamp>.",
  )
  parser.add_argument(
      "--no-save",
      action="store_true",
      help="Parse and measure frames without writing JPEG files.",
  )
  parser.add_argument(
      "--chunk-size",
      type=int,
      default=8192,
      help="HTTP read chunk size in bytes.",
  )
  parser.add_argument(
      "--report-every",
      type=int,
      default=30,
      help="Print one progress line every N frames.",
  )
  return parser.parse_args()


def utc_stamp() -> str:
  return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def fetch_status(status_url: str, timeout: float = 3.0) -> dict[str, object] | None:
  try:
    with urlopen(status_url, timeout=timeout) as response:
      body = response.read()
  except URLError:
    return None
  try:
    return json.loads(body.decode("utf-8"))
  except (json.JSONDecodeError, UnicodeDecodeError):
    return None


def prepare_recording(args: argparse.Namespace, status: dict[str, object] | None) -> tuple[Path, BinaryIO]:
  output_dir = args.output_dir or Path("recordings") / utc_stamp()
  frames_dir = output_dir / "frames"
  frames_dir.mkdir(parents=True, exist_ok=True)

  metadata = {
      "created_utc": utc_stamp(),
      "video_url": args.url,
      "status_url": args.status_url,
      "duration_seconds": args.duration,
      "max_frames": args.max_frames,
      "save_frames": not args.no_save,
      "camera_status": status,
  }
  (output_dir / "metadata.json").write_text(
      json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
  )

  csv_file = (output_dir / "frames.csv").open("w", newline="", encoding="utf-8")
  return output_dir, csv_file


def jpeg_dimensions(jpeg: bytes) -> tuple[int | None, int | None]:
  """Return JPEG width/height by scanning SOF markers."""
  idx = 2
  size = len(jpeg)

  while idx + 9 < size:
    if jpeg[idx] != 0xFF:
      idx += 1
      continue

    while idx < size and jpeg[idx] == 0xFF:
      idx += 1
    if idx >= size:
      break

    marker = jpeg[idx]
    idx += 1
    if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
      continue
    if idx + 2 > size:
      break

    segment_length = int.from_bytes(jpeg[idx:idx + 2], "big")
    if segment_length < 2 or idx + segment_length > size:
      break

    if marker in {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }:
      if segment_length >= 7:
        height = int.from_bytes(jpeg[idx + 3:idx + 5], "big")
        width = int.from_bytes(jpeg[idx + 5:idx + 7], "big")
        return width, height
      return None, None

    idx += segment_length

  return None, None


def iter_jpegs(response: BinaryIO, chunk_size: int):
  buffer = bytearray()
  while not stop_requested:
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


def print_progress(frames: list[FrameStats], started_ns: int) -> None:
  elapsed_s = (time.monotonic_ns() - started_ns) / 1_000_000_000
  if elapsed_s <= 0:
    return
  latest = frames[-1]
  fps = len(frames) / elapsed_s
  print(
      f"frames={len(frames)} fps={fps:.1f} "
      f"last={latest.width}x{latest.height} bytes={latest.byte_count}"
  )


def main() -> int:
  signal.signal(signal.SIGINT, handle_stop)
  signal.signal(signal.SIGTERM, handle_stop)

  args = parse_args()
  status = fetch_status(args.status_url)
  output_dir, csv_file = prepare_recording(args, status)

  frames: list[FrameStats] = []
  frames_dir = output_dir / "frames"

  print(f"stream: {args.url}")
  print(f"recording: {output_dir}")
  if status is not None:
    print(
        "camera: "
        f"framesize={status.get('framesize')} "
        f"quality={status.get('quality')} "
        f"pixformat={status.get('pixformat')}"
    )
  else:
    print("camera: status unavailable")

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

  request = Request(args.url, headers={"User-Agent": "elegoo-video-probe/0.1"})
  started_ns = time.monotonic_ns()
  deadline_ns = None
  if args.duration > 0:
    deadline_ns = started_ns + int(args.duration * 1_000_000_000)

  try:
    with urlopen(request, timeout=5) as response:
      content_type = response.headers.get("Content-Type", "")
      print(f"content-type: {content_type}")

      for frame in iter_jpegs(response, args.chunk_size):
        now_ns = time.monotonic_ns()
        width, height = jpeg_dimensions(frame)
        decode_ok = width is not None and height is not None
        frame_id = len(frames)
        filename = ""

        if not args.no_save:
          filename = f"frames/{frame_id:06d}.jpg"
          (frames_dir / f"{frame_id:06d}.jpg").write_bytes(frame)

        stats = FrameStats(
            frame_id=frame_id,
            monotonic_ns=now_ns,
            filename=filename,
            width=width,
            height=height,
            byte_count=len(frame),
            decode_ok=decode_ok,
        )
        frames.append(stats)
        writer.writerow({
            "frame_id": stats.frame_id,
            "monotonic_ns": stats.monotonic_ns,
            "filename": stats.filename,
            "width": stats.width if stats.width is not None else "",
            "height": stats.height if stats.height is not None else "",
            "byte_count": stats.byte_count,
            "decode_ok": str(stats.decode_ok).lower(),
        })

        if args.report_every > 0 and len(frames) % args.report_every == 0:
          print_progress(frames, started_ns)

        if args.max_frames > 0 and len(frames) >= args.max_frames:
          break
        if deadline_ns is not None and now_ns >= deadline_ns:
          break

  except URLError as exc:
    print(f"error: failed to read stream: {exc}", file=sys.stderr)
    return 2
  finally:
    csv_file.close()

  if frames:
    print_progress(frames, started_ns)
  else:
    print("frames=0")
    return 1

  return 0


if __name__ == "__main__":
  raise SystemExit(main())

