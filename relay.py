"""Run on the Linux VPS: python3 relay.py --port 5000."""
import argparse
import asyncio
from dataclasses import dataclass, field
import socket
import uuid

from protocol import MAX_LINE, TIMEOUT, decode, encode, mouse_event, state_event


@dataclass
class Source:
    name: str
    buttons: int = 0
    listeners: set = field(default_factory=set)


class Relay:
    def __init__(self):
        self.sources = {}

    async def read(self, reader, timeout=TIMEOUT):
        return decode(await asyncio.wait_for(reader.readline(), timeout))

    async def write(self, writer, message):
        writer.write(encode(message))
        await asyncio.wait_for(writer.drain(), 2)

    def publish(self, source, message):
        for queue in tuple(source.listeners):
            if queue.full():
                # Never silently discard a button release or replay stale motion.
                source.listeners.discard(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({"type": "error", "message": "Destination too slow; reconnect"})
            else:
                queue.put_nowait(message)

    async def handle(self, reader, writer):
        source_id = None
        subscription = None
        queue = None
        try:
            writer.get_extra_info("socket").setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            hello = await self.read(reader)
            if hello["type"] == "source":
                name = hello.get("name")
                if not isinstance(name, str) or not 1 <= len(name) <= 256 or not name.isprintable():
                    raise ValueError("Invalid source name")
                source_id = uuid.uuid4().hex
                source = Source(name)
                self.sources[source_id] = source
                await self.write(writer, {"type": "registered", "id": source_id})
                print(f"Source connected: {name}", flush=True)
                while True:
                    event = await self.read(reader)
                    if event["type"] == "ping":
                        continue
                    event = mouse_event(event)
                    source.buttons = event["buttons"]
                    self.publish(source, event)
            elif hello["type"] == "destination":
                await self.write(writer, {"type": "sources", "sources": [
                    {"id": key, "name": value.name} for key, value in self.sources.items()]})
                # Allow time for a person to choose from the printed list.
                request = await self.read(reader, timeout=300)
                source_id_requested = request.get("id")
                if request["type"] != "subscribe" or not isinstance(source_id_requested, str):
                    raise ValueError("Expected a source selection")
                subscription = self.sources.get(source_id_requested)
                if subscription is None:
                    raise ValueError("Source is no longer available; reconnect")
                queue = asyncio.Queue(maxsize=2048)
                subscription.listeners.add(queue)
                queue.put_nowait(state_event(subscription.buttons))
                await self.write(writer, {"type": "subscribed", "name": subscription.name})
                while True:
                    try:
                        message = await asyncio.wait_for(queue.get(), 1)
                    except asyncio.TimeoutError:
                        message = {"type": "ping"}
                    await self.write(writer, message)
                    if message["type"] in ("offline", "error"):
                        break
            else:
                raise ValueError("Expected source or destination greeting")
        except (OSError, ValueError, ConnectionError, asyncio.TimeoutError) as exc:
            try:
                await self.write(writer, {"type": "error", "message": str(exc) or "Connection timed out"})
            except (OSError, asyncio.TimeoutError):
                pass
        finally:
            if source_id is not None:
                source = self.sources.pop(source_id, None)
                if source:
                    self.publish(source, {"type": "offline"})
                    print(f"Source disconnected: {source.name}", flush=True)
            if subscription is not None and queue is not None:
                subscription.listeners.discard(queue)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


async def serve(host, port):
    relay = Relay()
    server = await asyncio.start_server(relay.handle, host, port, limit=MAX_LINE)
    print(f"Relay listening on {host}:{port}. Ctrl+C to stop.", flush=True)
    async with server:
        await server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0", help="Address to listen on")
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
