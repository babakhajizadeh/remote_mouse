"""Control messages and lossless byte streams for the USB/IP reverse tunnel."""
import asyncio
import contextlib
import json
import re
import socket

VERSION = 2
MAX_LINE = 65536
TIMEOUT = 10


def encode(message):
    data = json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
    if len(data) > MAX_LINE:
        raise ValueError("Message is too large")
    return data


async def receive(reader, timeout=TIMEOUT):
    data = await asyncio.wait_for(reader.readline(), timeout)
    if not data:
        raise ConnectionError("Connection closed")
    if len(data) > MAX_LINE or not data.endswith(b"\n"):
        raise ValueError("Invalid control message length")
    message = json.loads(data)
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ValueError("Invalid control message")
    if message["type"] == "error":
        raise ConnectionError(message.get("message", "Relay error"))
    return message


async def send(writer, message):
    writer.write(encode(message))
    await asyncio.wait_for(writer.drain(), TIMEOUT)


def tune(writer):
    sock = writer.get_extra_info("socket")
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)


async def connect(host, port):
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, limit=MAX_LINE), TIMEOUT)
    tune(writer)
    return reader, writer


async def close(writer):
    if writer is not None:
        writer.close()
        with contextlib.suppress(OSError, asyncio.TimeoutError):
            await asyncio.wait_for(writer.wait_closed(), 2)


async def bridge(left_reader, left_writer, right_reader, right_writer):
    """Preserve bytes in both directions; end the session if either end exits."""
    async def copy(reader, writer):
        while data := await reader.read(65536):
            writer.write(data)
            await asyncio.wait_for(writer.drain(), TIMEOUT)

    tasks = [asyncio.create_task(copy(left_reader, right_writer)),
             asyncio.create_task(copy(right_reader, left_writer))]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def bus_id(value):
    if not isinstance(value, str) or len(value) > 31 or not re.fullmatch(r"[0-9]+-[0-9]+(?:\.[0-9]+)*", value):
        raise ValueError("Invalid USB bus ID")
    return value


def endpoint(args):
    host = args.host or input("VPS IP address: ").strip()
    if not host:
        raise ValueError("VPS IP address cannot be empty")
    port = args.port if args.port is not None else int(input("VPS port [5000]: ").strip() or "5000")
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    return host, port


def choose(items, label):
    if not items:
        raise ValueError(f"No {label} available")
    for index, item in enumerate(items, 1):
        print(f"[{index}] {item['name']} (USB bus {item['busid']})")
        if item.get("path"):
            print(f"    {item['path']}")
    index = int(input("Select device: "))
    if not 1 <= index <= len(items):
        raise ValueError("Invalid device selection")
    return items[index - 1]
