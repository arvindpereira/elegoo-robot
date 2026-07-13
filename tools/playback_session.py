#!/usr/bin/env python3
"""Inspect and replay metadata for a recorded ELEGOO robot session.

This is intentionally dependency-free. It validates a recording directory,
summarizes frame/control timing, and can replay the session timeline as text.
It does not connect to the robot.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FrameRow:
  frame_id: int
  monotonic_ns: int
  filename: str
  width: int | None
  height: int | None
  byte_count: int
  decode_ok: bool


@dataclass
class ControlEvent:
  monotonic_ns: int
  event: str
  payload: str


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="Inspect or replay a recorded session.")
  parser.add_argument("recording_dir", type=Path, help="Path to a recordings/<timestamp> directory.")
  parser.add_argument(
      "--replay",
      action="store_true",
      help="Replay the session timeline as text using recorded timing.",
  )
  parser.add_argument(
      "--speed",
      type=float,
      default=1.0,
      help="Replay speed multiplier. Values >1 replay faster.",
  )
  parser.add_argument(
      "--show-every",
      type=int,
      default=30,
      help="During replay, print every Nth frame event.",
  )
  parser.add_argument(
      "--validate-files",
      action="store_true",
      help="Check that referenced frame files exist.",
  )
  return parser.parse_args()


def parse_optional_int(value: str) -> int | None:
  if value == "":
    return None
  return int(value)


def read_frames(recording_dir: Path) -> list[FrameRow]:
  frames_path = recording_dir / "frames.csv"
  rows: list[FrameRow] = []
  with frames_path.open(newline="", encoding="utf-8") as csv_file:
    for row in csv.DictReader(csv_file):
      rows.append(
          FrameRow(
              frame_id=int(row["frame_id"]),
              monotonic_ns=int(row["monotonic_ns"]),
              filename=row["filename"],
              width=parse_optional_int(row["width"]),
              height=parse_optional_int(row["height"]),
              byte_count=int(row["byte_count"]),
              decode_ok=row["decode_ok"].lower() == "true",
          )
      )
  return rows


def read_control_events(recording_dir: Path) -> list[ControlEvent]:
  events_path = recording_dir / "control_events.csv"
  if not events_path.exists():
    return []

  events: list[ControlEvent] = []
  with events_path.open(newline="", encoding="utf-8") as csv_file:
    for row in csv.DictReader(csv_file):
      events.append(
          ControlEvent(
              monotonic_ns=int(row["monotonic_ns"]),
              event=row["event"],
              payload=row["payload"],
          )
      )
  return events


def read_json(path: Path) -> dict[str, object]:
  if not path.exists():
    return {}
  return json.loads(path.read_text(encoding="utf-8"))


def summarize_frames(frames: list[FrameRow]) -> dict[str, object]:
  if not frames:
    return {"frames": 0}

  first_ns = frames[0].monotonic_ns
  last_ns = frames[-1].monotonic_ns
  duration_s = max((last_ns - first_ns) / 1_000_000_000, 0.0)
  fps = len(frames) / duration_s if duration_s > 0 else 0.0
  deltas_ms = [
      (frames[index].monotonic_ns - frames[index - 1].monotonic_ns) / 1_000_000
      for index in range(1, len(frames))
  ]

  widths = sorted({frame.width for frame in frames if frame.width is not None})
  heights = sorted({frame.height for frame in frames if frame.height is not None})
  corrupt = sum(1 for frame in frames if not frame.decode_ok)

  summary: dict[str, object] = {
      "frames": len(frames),
      "duration_s": duration_s,
      "fps": fps,
      "widths": widths,
      "heights": heights,
      "corrupt_frames": corrupt,
      "avg_bytes": sum(frame.byte_count for frame in frames) / len(frames),
  }

  if deltas_ms:
    summary.update({
        "frame_delta_ms_min": min(deltas_ms),
        "frame_delta_ms_avg": statistics.mean(deltas_ms),
        "frame_delta_ms_max": max(deltas_ms),
    })

  return summary


def summarize_control(events: list[ControlEvent]) -> dict[str, object]:
  counts: dict[str, int] = {}
  for event in events:
    counts[event.event] = counts.get(event.event, 0) + 1

  return {
      "control_events": len(events),
      "event_counts": counts,
  }


def validate_frame_files(recording_dir: Path, frames: list[FrameRow]) -> tuple[int, int]:
  checked = 0
  missing = 0
  for frame in frames:
    if not frame.filename:
      continue
    checked += 1
    if not (recording_dir / frame.filename).exists():
      missing += 1
  return checked, missing


def print_summary(recording_dir: Path, frames: list[FrameRow], events: list[ControlEvent], args: argparse.Namespace) -> None:
  metadata = read_json(recording_dir / "metadata.json")
  summary_json = read_json(recording_dir / "summary.json")
  frame_summary = summarize_frames(frames)
  control_summary = summarize_control(events)

  print(f"recording: {recording_dir}")
  if metadata.get("created_utc"):
    print(f"created_utc: {metadata['created_utc']}")
  if metadata.get("video_url"):
    print(f"video_url: {metadata['video_url']}")

  print(
      "frames: "
      f"{frame_summary.get('frames', 0)} "
      f"duration_s={float(frame_summary.get('duration_s', 0.0)):.2f} "
      f"fps={float(frame_summary.get('fps', 0.0)):.2f}"
  )
  if frame_summary.get("widths") and frame_summary.get("heights"):
    print(f"resolution_values: {frame_summary['widths']}x{frame_summary['heights']}")
  if "frame_delta_ms_avg" in frame_summary:
    print(
        "frame_delta_ms: "
        f"min={float(frame_summary['frame_delta_ms_min']):.2f} "
        f"avg={float(frame_summary['frame_delta_ms_avg']):.2f} "
        f"max={float(frame_summary['frame_delta_ms_max']):.2f}"
    )
  print(f"avg_frame_bytes: {float(frame_summary.get('avg_bytes', 0.0)):.0f}")
  print(f"corrupt_frames: {frame_summary.get('corrupt_frames', 0)}")

  print(
      "control: "
      f"{control_summary['control_events']} events "
      f"{control_summary['event_counts']}"
  )
  if summary_json:
    print(
        "recorded_summary: "
        f"heartbeats={summary_json.get('heartbeats_received', 0)} "
        f"stops={summary_json.get('stop_commands_sent', 0)}"
    )

  if args.validate_files:
    checked, missing = validate_frame_files(recording_dir, frames)
    print(f"frame_files: checked={checked} missing={missing}")


def replay(frames: list[FrameRow], events: list[ControlEvent], speed: float, show_every: int) -> None:
  if speed <= 0:
    raise ValueError("--speed must be greater than 0")

  timeline: list[tuple[int, str, object]] = []
  for frame in frames:
    timeline.append((frame.monotonic_ns, "frame", frame))
  for event in events:
    timeline.append((event.monotonic_ns, "control", event))
  timeline.sort(key=lambda item: item[0])

  if not timeline:
    print("nothing to replay")
    return

  base_recorded_ns = timeline[0][0]
  base_wall = time.monotonic()

  for timestamp_ns, kind, item in timeline:
    elapsed_recorded_s = (timestamp_ns - base_recorded_ns) / 1_000_000_000
    target_wall = base_wall + elapsed_recorded_s / speed
    while True:
      remaining = target_wall - time.monotonic()
      if remaining <= 0:
        break
      time.sleep(min(remaining, 0.05))

    if kind == "frame":
      frame = item
      assert isinstance(frame, FrameRow)
      if show_every > 0 and frame.frame_id % show_every != 0:
        continue
      print(
          f"t={elapsed_recorded_s:.3f}s frame={frame.frame_id} "
          f"{frame.width}x{frame.height} bytes={frame.byte_count} file={frame.filename}",
          flush=True,
      )
    else:
      event = item
      assert isinstance(event, ControlEvent)
      print(
          f"t={elapsed_recorded_s:.3f}s control={event.event} payload={event.payload}",
          flush=True,
      )


def main() -> int:
  args = parse_args()
  recording_dir = args.recording_dir
  if not recording_dir.exists():
    print(f"error: recording directory does not exist: {recording_dir}")
    return 2
  if not (recording_dir / "frames.csv").exists():
    print(f"error: missing frames.csv in {recording_dir}")
    return 2

  frames = read_frames(recording_dir)
  events = read_control_events(recording_dir)
  print_summary(recording_dir, frames, events, args)

  if args.replay:
    replay(frames, events, args.speed, args.show_every)

  return 0


if __name__ == "__main__":
  raise SystemExit(main())

