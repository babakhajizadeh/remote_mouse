"""Export one physical USB mouse through a reverse tunnel to the VPS."""
import argparse
import asyncio
import socket
import subprocess
import sys
import traceback

from protocol import VERSION, check_relay, choose, close, connect, endpoint, receive, send
from usb_backend import command, list_devices, require_windows, tool
from usb_tunnel import forward_device


async def share(host, port, device, usb_port=3240, on_registered=None):
    print(f"Connecting to VPS {host}:{port}...", flush=True)
    reader, writer = await connect(host, port)
    channels = set()
    heartbeat = None

    async def channel(token):
        channel_writer = None
        try:
            channel_reader, channel_writer = await connect(host, port)
            await send(channel_writer, {"type": "channel", "version": VERSION, "token": token})
            if (await receive(channel_reader))["type"] != "ready":
                raise ValueError("Unexpected relay channel response")
            await forward_device(channel_reader, channel_writer, device["busid"], usb_port=usb_port)
        except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
            print(f"USB tunnel closed: {exc}", file=sys.stderr)
        finally:
            await close(channel_writer)

    async def ping():
        try:
            while True:
                await send(writer, {"type": "ping"})
                await asyncio.sleep(2)
        except OSError:
            writer.close()

    try:
        name = f"{socket.gethostname()} / {device['name']}"[:256]
        await send(writer, {"type": "source", "version": VERSION, "name": name, "busid": device["busid"]})
        response = await receive(reader)
        if response["type"] != "registered":
            raise ValueError("Unexpected relay response")
        if on_registered:
            on_registered(response["id"])
        print(f"Sharing physical USB device {device['name']} [{device['busid']}] through {host}:{port}.")
        print("Keep this window open. Ctrl+C stops forwarding and returns the device.")
        heartbeat = asyncio.create_task(ping())
        while True:
            message = await receive(reader)
            if message["type"] == "pong":
                continue
            if message["type"] != "open" or not isinstance(message.get("token"), str):
                raise ValueError("Unexpected relay command")
            task = asyncio.create_task(channel(message["token"]))
            channels.add(task)
            task.add_done_callback(channels.discard)
    finally:
        if heartbeat:
            heartbeat.cancel()
        for task in tuple(channels):
            task.cancel()
        await asyncio.gather(*(list(channels) + ([heartbeat] if heartbeat else [])), return_exceptions=True)
        await close(writer)


def run(args):
    require_windows()
    usbipd = tool("usbipd", args.usbipd)
    print("Available physical USB mice/receivers:" if not args.all_usb else "Available physical USB devices:")
    print("The entire selected USB device is handed over to the Surface while attached.")
    devices = list_devices(usbipd, args.all_usb)
    device = choose(devices, "USB mice (use --all-usb to list receivers not identified as mice)")
    if device["attached"]:
        raise ValueError("This device is already attached to another USB/IP client")
    host, port = endpoint(args)
    print(f"Checking VPS relay at {host}:{port} before sharing the device...", flush=True)
    asyncio.run(check_relay(host, port))
    print("VPS relay is reachable and compatible.", flush=True)
    bound_here = False
    try:
        if not device["shared"]:
            print(f"Sharing USB bus {device['busid']} with usbipd (requires Administrator).")
            options = ["--force"] if args.force else []
            command(usbipd, "bind", "--busid", device["busid"], *options)
            bound_here = True
        asyncio.run(share(host, port, device, args.usbipd_port))
    finally:
        if bound_here:
            try:
                command(usbipd, "unbind", "--busid", device["busid"])
            except (OSError, subprocess.SubprocessError) as exc:
                print(f"Could not undo sharing: {exc}\nRun: usbipd unbind --busid {device['busid']}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="VPS IP address; prompted when omitted")
    parser.add_argument("--port", type=int, help="VPS TCP port; prompted when omitted")
    parser.add_argument("--usbipd", help="Path to usbipd.exe")
    parser.add_argument("--usbipd-port", type=int, default=3240, help="Local usbipd TCP port")
    parser.add_argument("--all-usb", action="store_true", help="Show all USB devices, including unidentified receivers")
    parser.add_argument("--force", action="store_true", help="Use usbipd bind --force; replaces the device's local driver while shared")
    parser.add_argument("--debug", action="store_true", help="Include the full traceback when an error occurs")
    args = parser.parse_args()
    try:
        run(args)
    except (KeyboardInterrupt, EOFError):
        print("\nUSB forwarding stopped.")
    except (OSError, ValueError, subprocess.SubprocessError, asyncio.TimeoutError) as exc:
        if args.debug:
            traceback.print_exc()
        parser.exit(1, f"Source error: {str(exc) or type(exc).__name__}\n")


if __name__ == "__main__":
    main()
