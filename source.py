"""Choose a local Windows mouse and forward its input to the VPS."""
import argparse
import os
import queue
import socket
import threading

from protocol import Connection, choose, endpoint


def run(args):
    if os.name != "nt":
        raise ValueError("The source requires Windows")
    from winmouse import capture, list_mice
    print("Available local mouse devices:")
    device = choose(list_mice(), "mouse devices")
    host, port = endpoint(args)
    connection = Connection(host, port)
    stop = threading.Event()
    events = queue.Queue(maxsize=4096)
    errors = []
    worker = None
    try:
        connection.send({"type": "source", "name": f"{socket.gethostname()} / {device['name']}"[:256]})
        if connection.receive()["type"] != "registered":
            raise ValueError("Unexpected relay response")

        def send_events():
            try:
                while not stop.is_set():
                    try:
                        event = events.get(timeout=1)
                    except queue.Empty:
                        event = {"type": "ping"}
                    connection.send(event)
            except (OSError, ValueError) as exc:
                errors.append(exc)
                stop.set()

        def emit(event):
            try:
                events.put_nowait(event)
            except queue.Full:
                raise ConnectionError("Network cannot keep up with the mouse; forwarding stopped")

        worker = threading.Thread(target=send_events, daemon=True)
        worker.start()
        print(f"Sharing {device['name']} through {host}:{port}.")
        print("Press Ctrl+Alt+F12 anywhere or Ctrl+C in this console to stop.")
        capture(device["handle"], emit, stop)
        if errors:
            raise errors[0]
    finally:
        stop.set()
        connection.close()
        if worker:
            worker.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="VPS IP address; prompted when omitted")
    parser.add_argument("--port", type=int, help="VPS TCP port; prompted when omitted")
    args = parser.parse_args()
    try:
        run(args)
    except (KeyboardInterrupt, EOFError):
        print("\nSharing stopped.")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Source error: {exc}\n")


if __name__ == "__main__":
    main()
