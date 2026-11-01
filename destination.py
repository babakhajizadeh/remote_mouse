"""Connect to the VPS, select a shared mouse, and control this Windows cursor."""
import argparse
import os
import queue
import threading

from protocol import Connection, choose, endpoint, mouse_event


def run(args):
    if os.name != "nt":
        raise ValueError("The destination requires Windows")
    from winmouse import Injector, stop_pressed
    host, port = endpoint(args)
    connection = Connection(host, port)
    injector = Injector()
    stop = threading.Event()
    events = queue.Queue(maxsize=2048)
    errors = []
    worker = None
    try:
        connection.send({"type": "destination"})
        listing = connection.receive()
        if listing["type"] != "sources":
            raise ValueError("Unexpected relay response")
        print("Available remote mice:")
        source = choose(listing["sources"], "remote mice; start the source and reconnect")
        connection.send({"type": "subscribe", "id": source["id"]})
        if connection.receive()["type"] != "subscribed":
            raise ValueError("Unexpected relay response")

        def receive_events():
            try:
                while not stop.is_set():
                    message = connection.receive()
                    if message["type"] == "ping":
                        continue
                    if message["type"] == "offline":
                        raise ConnectionError("Source disconnected")
                    events.put_nowait(mouse_event(message))
            except (OSError, ValueError, queue.Full) as exc:
                errors.append(exc if not isinstance(exc, queue.Full) else
                              ConnectionError("Input backlog too large; reconnect"))
                stop.set()

        worker = threading.Thread(target=receive_events, daemon=True)
        worker.start()
        print(f"Connected to {source['name']}.")
        print("Press Ctrl+Alt+F12 anywhere or Ctrl+C in this console to stop.")
        while not stop.is_set():
            if stop_pressed():
                break
            try:
                event = events.get(timeout=0.02)
            except queue.Empty:
                continue
            injector.apply(event)
        if errors:
            raise errors[0]
    finally:
        stop.set()
        # Shut down the socket before waiting for its reader thread.
        connection.close()
        if worker:
            worker.join(timeout=2)
        injector.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="VPS IP address; prompted when omitted")
    parser.add_argument("--port", type=int, help="VPS TCP port; prompted when omitted")
    args = parser.parse_args()
    try:
        run(args)
    except (KeyboardInterrupt, EOFError):
        print("\nDisconnected.")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Destination error: {exc}\n")


if __name__ == "__main__":
    main()
