"""Attach a physical remote USB device to Windows through the VPS."""
import argparse
import asyncio
import re
import subprocess
import sys

from protocol import VERSION, bridge, choose, close, connect, endpoint, receive, send, tune
from usb_backend import command, require_windows, tool


async def list_sources(host, port):
    reader, writer = await connect(host, port)
    try:
        await send(writer, {"type": "list", "version": VERSION})
        message = await receive(reader)
        if message["type"] != "sources":
            raise ValueError("Unexpected relay response")
        return message["sources"]
    finally:
        await close(writer)


class LocalProxy:
    """The native USB/IP driver connects here; this opens an outbound VPS stream."""
    def __init__(self, host, port, source_id):
        self.host, self.port, self.source_id = host, port, source_id
        self.tasks = set()
        self.disconnected = asyncio.Event()
        self.error = None
        self.server = None

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        remote_writer = None
        try:
            tune(writer)
            remote_reader, remote_writer = await connect(self.host, self.port)
            await send(remote_writer, {"type": "connect", "version": VERSION, "id": self.source_id})
            if (await receive(remote_reader))["type"] != "ready":
                raise ValueError("Unexpected relay tunnel response")
            await bridge(reader, writer, remote_reader, remote_writer)
        except (OSError, ValueError, asyncio.TimeoutError) as exc:
            self.error = str(exc) or "Tunnel timed out"
        finally:
            await close(remote_writer)
            await close(writer)
            self.tasks.discard(task)
            self.disconnected.set()

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_tool(*args):
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(process.communicate(), 40)
    except BaseException:
        if process.returncode is None:
            process.terminate()
            await process.wait()
        raise
    output = out.decode("utf-8-sig", errors="replace").strip()
    if process.returncode:
        raise OSError(err.decode("utf-8-sig", errors="replace").strip() or output or "USB/IP command failed")
    return output


def attached_port(output):
    if not re.fullmatch(r"[0-9]+", output.strip()):
        raise ValueError(f"Could not identify the attached USB/IP port: {output!r}")
    value = int(output)
    if not 1 <= value <= 255:
        raise ValueError("Invalid attached USB/IP port")
    return value


async def attach(host, port, source, usbip, once=False):
    proxy = LocalProxy(host, port, source["id"])
    attached = None
    try:
        local_port = await proxy.start()
        arguments = [usbip, "--tcp-port", str(local_port), "attach",
                     "-r", "127.0.0.1", "-b", source["busid"], "--terse"]
        if once:
            arguments.append("--once")
        attached = attached_port(await run_tool(*arguments))
        print(f"Physical USB device attached on Windows USB/IP port {attached}: {source['name']}")
        print("Keep this window open. Ctrl+C detaches this device.")
        await proxy.disconnected.wait()
        raise ConnectionError(proxy.error or "USB device disconnected")
    finally:
        # Closing the byte stream also terminates an import if attachment was interrupted.
        await proxy.stop()
        if attached is not None:
            try:
                await run_tool(usbip, "detach", "-p", str(attached))
            except (OSError, asyncio.TimeoutError) as exc:
                print(f"Detach reported: {exc}\nCheck: usbip port", file=sys.stderr)


def run(args):
    require_windows()
    usbip = tool("usbip", args.usbip)
    # New clients retry automatically; --once prevents background retries on exit.
    help_text = command(usbip, "attach", "--help")
    host, port = endpoint(args)
    sources = asyncio.run(list_sources(host, port))
    print("Available remote physical USB devices:")
    source = choose([item for item in sources if not item["busy"]],
                    "free remote USB devices; start the Desktop source first")
    asyncio.run(attach(host, port, source, usbip, once="--once" in help_text))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="VPS IP address; prompted when omitted")
    parser.add_argument("--port", type=int, help="VPS TCP port; prompted when omitted")
    parser.add_argument("--usbip", help="Path to usbip.exe from usbip-win2")
    args = parser.parse_args()
    try:
        run(args)
    except (KeyboardInterrupt, EOFError):
        print("\nUSB device detached.")
    except (OSError, ValueError, subprocess.SubprocessError, asyncio.TimeoutError) as exc:
        parser.exit(1, f"Destination error: {exc}\n")


if __name__ == "__main__":
    main()
