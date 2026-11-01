"""Small, bounded JSON-lines protocol shared by all three programs."""
import json
import socket

MAX_LINE = 4096
TIMEOUT = 6


def encode(message):
    data = json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
    if len(data) > MAX_LINE:
        raise ValueError("Message is too large")
    return data


def decode(data):
    if not data:
        raise ConnectionError("Connection closed")
    if len(data) > MAX_LINE or not data.endswith(b"\n"):
        raise ValueError("Invalid message length")
    message = json.loads(data)
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ValueError("Expected a message object with a type")
    if message["type"] == "error":
        raise ConnectionError(message.get("message", "Relay error"))
    return message


def mouse_event(message):
    """Validate before replaying input; never forward arbitrary native flags."""
    if message.get("type") != "mouse":
        raise ValueError("Expected mouse input")
    result = {"type": "mouse"}
    for key, low, high in (("dx", -2147483648, 2147483647),
                           ("dy", -2147483648, 2147483647),
                           ("buttons", 0, 31), ("wheel", -32768, 32767),
                           ("hwheel", -32768, 32767)):
        value = message.get(key)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"Invalid {key}")
        result[key] = value
    for key in ("absolute", "virtual"):
        if type(message.get(key)) is not bool:
            raise ValueError(f"Invalid {key}")
        result[key] = message[key]
    if result["absolute"] and not (0 <= result["dx"] <= 65535 and 0 <= result["dy"] <= 65535):
        raise ValueError("Absolute coordinates must be in 0..65535")
    return result


def state_event(buttons=0):
    return dict(type="mouse", dx=0, dy=0, buttons=buttons, wheel=0,
                hwheel=0, absolute=False, virtual=False)


class Connection:
    def __init__(self, host, port):
        self.socket = socket.create_connection((host, port), timeout=TIMEOUT)
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.reader = self.socket.makefile("rb")

    def send(self, message):
        self.socket.sendall(encode(message))

    def receive(self):
        return decode(self.reader.readline(MAX_LINE + 1))

    def close(self):
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.reader.close()
        self.socket.close()


def endpoint(args):
    host = args.host or input("VPS IP address: ").strip()
    if not host:
        raise ValueError("VPS IP address cannot be empty")
    port = args.port or int(input("VPS port [5000]: ").strip() or "5000")
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    return host, port


def choose(items, label):
    if not items:
        raise ValueError(f"No {label} available")
    for index, item in enumerate(items, 1):
        print(f"[{index}] {item['name']}")
        if item.get("path"):
            print(f"    {item['path']}")
    index = int(input("Select mouse: "))
    if not 1 <= index <= len(items):
        raise ValueError("Invalid mouse selection")
    return items[index - 1]
