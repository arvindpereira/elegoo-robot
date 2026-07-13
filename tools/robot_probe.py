#!/usr/bin/env python3
"""Heartbeat-only probe for the ELEGOO ESP32 robot control socket.

Default behavior is intentionally non-driving: connect, print messages, and
reply to `{Heartbeat}`. It does not send motor commands.
"""

from __future__ import annotations

import argparse
import json
import signal
import socket
import sys
import time
from dataclasses import dataclass


DEFAULT_HOST = "192.168.4.1"
DEFAULT_PORT = 100
HEARTBEAT = b"{Heartbeat}"
STOP_COMMAND = b'{"N":100}'


stop_requested = False


@dataclass
class ProbeStats:
  connected_at_ns: int
  received_messages: int = 0
  heartbeats_received: int = 0
  heartbeats_sent: int = 0
  stop_commands_sent: int = 0
  bytes_received: int = 0


def handle_stop(signum: int, frame: object) -> None:
  del signum, frame
  global stop_requested
  stop_requested = True


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(
      description="Connect to the ELEGOO ESP32 control socket and reply to heartbeats."
  )
  parser.add_argument("--host", default=DEFAULT_HOST, help="ESP32 control host.")
  parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="ESP32 control port.")
  parser.add_argument(
      "--duration",
      type=float,
      default=10.0,
      help="Seconds to run. Use 0 to run until interrupted.",
  )
  parser.add_argument(
      "--connect-timeout",
      type=float,
      default=5.0,
      help="TCP connection timeout in seconds.",
  )
  parser.add_argument(
      "--read-timeout",
      type=float,
      default=1.0,
      help="Socket read timeout in seconds.",
  )
  parser.add_argument(
      "--send-stop",
      action="store_true",
      help=(
          "Send the stock stop/clear command {\"N\":100} on startup and shutdown. "
          "Default heartbeat-only mode sends no UNO commands."
      ),
  )
  parser.add_argument(
      "--json-log",
      action="store_true",
      help="Print events as JSON lines instead of human-readable text.",
  )
  return parser.parse_args()


def log_event(args: argparse.Namespace, event: str, **fields: object) -> None:
  if args.json_log:
    payload = {"event": event, "monotonic_ns": time.monotonic_ns(), **fields}
    print(json.dumps(payload, sort_keys=True), flush=True)
  else:
    if fields:
      detail = " ".join(f"{key}={value}" for key, value in fields.items())
      print(f"{event}: {detail}", flush=True)
    else:
      print(event, flush=True)


def send_stop(sock: socket.socket, stats: ProbeStats, args: argparse.Namespace, reason: str) -> None:
  sock.sendall(STOP_COMMAND)
  stats.stop_commands_sent += 1
  log_event(args, "sent_stop", reason=reason, command=STOP_COMMAND.decode("ascii"))


def process_buffer(sock: socket.socket, buffer: bytearray, stats: ProbeStats, args: argparse.Namespace) -> None:
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

    stats.received_messages += 1
    text = message.decode("utf-8", "replace")
    log_event(args, "recv", message=text)

    if message == HEARTBEAT:
      sock.sendall(HEARTBEAT)
      stats.heartbeats_received += 1
      stats.heartbeats_sent += 1
      log_event(args, "sent_heartbeat")


def run_probe(args: argparse.Namespace) -> int:
  stats = ProbeStats(connected_at_ns=time.monotonic_ns())
  deadline_ns = None
  if args.duration > 0:
    deadline_ns = stats.connected_at_ns + int(args.duration * 1_000_000_000)

  address = (args.host, args.port)
  log_event(args, "connecting", host=args.host, port=args.port)

  with socket.create_connection(address, timeout=args.connect_timeout) as sock:
    sock.settimeout(args.read_timeout)
    log_event(args, "connected", host=args.host, port=args.port)

    if args.send_stop:
      send_stop(sock, stats, args, "startup")

    buffer = bytearray()
    while not stop_requested:
      if deadline_ns is not None and time.monotonic_ns() >= deadline_ns:
        break

      try:
        data = sock.recv(1024)
      except socket.timeout:
        continue

      if not data:
        log_event(args, "disconnected_by_peer")
        break

      stats.bytes_received += len(data)
      buffer.extend(data)
      process_buffer(sock, buffer, stats, args)

    if args.send_stop:
      send_stop(sock, stats, args, "shutdown")

  elapsed_s = (time.monotonic_ns() - stats.connected_at_ns) / 1_000_000_000
  log_event(
      args,
      "summary",
      elapsed_s=f"{elapsed_s:.2f}",
      received_messages=stats.received_messages,
      heartbeats_received=stats.heartbeats_received,
      heartbeats_sent=stats.heartbeats_sent,
      stop_commands_sent=stats.stop_commands_sent,
      bytes_received=stats.bytes_received,
  )
  return 0


def main() -> int:
  signal.signal(signal.SIGINT, handle_stop)
  signal.signal(signal.SIGTERM, handle_stop)
  args = parse_args()

  try:
    return run_probe(args)
  except OSError as exc:
    print(f"error: control socket failed: {exc}", file=sys.stderr)
    return 2


if __name__ == "__main__":
  raise SystemExit(main())

