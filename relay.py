"""Linux VPS rendezvous/byte relay for real USB/IP devices (no USB drivers here)."""
import argparse
import asyncio
from dataclasses import dataclass
import uuid

from protocol import VERSION, MAX_LINE, bridge, bus_id, close, receive, send, tune


@dataclass
class Source:
    name: str
    busid: str
    writer: object
    active: object = None


class Relay:
    def __init__(self):
        self.sources = {}
        self.pending = {}

    async def handle(self, reader, writer):
        source_id = None
        raw = False
        try:
            tune(writer)
            hello = await receive(reader)
            if hello.get("version") != VERSION:
                raise ValueError("Protocol changed: update the VPS and both Windows clients")
            role = hello["type"]
            if role == "source":
                name = hello.get("name")
                if not isinstance(name, str) or not 1 <= len(name) <= 256 or not name.isprintable():
                    raise ValueError("Invalid source name")
                if len(self.sources) >= 32:
                    raise ValueError("Relay source limit reached")
                source = Source(name, bus_id(hello.get("busid")), writer)
                source_id = uuid.uuid4().hex
                self.sources[source_id] = source
                await send(writer, {"type": "registered", "id": source_id})
                print(f"USB source connected: {name} [{source.busid}]", flush=True)
                while True:
                    message = await receive(reader)
                    if message["type"] != "ping":
                        raise ValueError("Expected source heartbeat")
                    await send(writer, {"type": "pong"})
            elif role == "list":
                await send(writer, {"type": "sources", "sources": [
                    {"id": key, "name": item.name, "busid": item.busid,
                     "busy": item.active is not None} for key, item in self.sources.items()]})
            elif role == "connect":
                selected = hello.get("id")
                if not isinstance(selected, str) or selected not in self.sources:
                    raise ValueError("USB source is no longer available")
                source = self.sources[selected]
                if source.active is not None:
                    raise ValueError("USB device is already in use by a destination")
                source.active = asyncio.current_task()
                token = uuid.uuid4().hex
                future = asyncio.get_running_loop().create_future()
                complete = asyncio.Event()
                self.pending[token] = (future, complete)
                try:
                    await send(source.writer, {"type": "open", "token": token})
                    remote_reader, remote_writer = await asyncio.wait_for(future, 10)
                    await send(writer, {"type": "ready"})
                    raw = True
                    await bridge(reader, writer, remote_reader, remote_writer)
                finally:
                    self.pending.pop(token, None)
                    complete.set()
                    source.active = None
            elif role == "channel":
                token = hello.get("token")
                pending = self.pending.get(token) if isinstance(token, str) else None
                if pending is None or pending[0].done():
                    raise ValueError("Unknown or expired tunnel channel")
                future, complete = pending
                await send(writer, {"type": "ready"})
                raw = True
                # The connect handler owns all subsequent reads on this stream.
                if not future.done():
                    future.set_result((reader, writer))
                    await complete.wait()
            else:
                raise ValueError("Unknown connection role")
        except (OSError, ValueError, asyncio.TimeoutError) as exc:
            if not raw:
                try:
                    await send(writer, {"type": "error", "message": str(exc) or "Connection timed out"})
                except (OSError, asyncio.TimeoutError):
                    pass
        finally:
            if source_id is not None:
                source = self.sources.pop(source_id, None)
                if source:
                    if source.active is not None:
                        source.active.cancel()
                    print(f"USB source disconnected: {source.name}", flush=True)
            await close(writer)


async def serve(host, port):
    relay = Relay()
    server = await asyncio.start_server(relay.handle, host, port, limit=MAX_LINE)
    print(f"Relay listening on {host}:{port}. Ctrl+C to stop.", flush=True)
    async with server:
        await server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.host, args.port))
    except KeyboardInterrupt:
        print("\nRelay stopped.")
    except OSError as exc:
        parser.exit(1, f"Relay error: {exc}\n")


if __name__ == "__main__":
    main()
