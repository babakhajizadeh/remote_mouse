# Remote mouse through a VPS

Three Python programs share one selected Windows mouse through a Linux VPS.
Both Windows computers make outbound TCP connections to the VPS. Python 3.10+
is required; there are no third-party dependencies.

The source uses Windows Raw Input to distinguish physical mice. The destination
replays movement, five mouse buttons, and vertical/horizontal scrolling using
Windows SendInput. This controls the normal Windows cursor; it does **not**
install a virtual HID device or forward arbitrary USB/HID reports.

## 1. VPS

Copy `vps.sh`, `relay.py`, and `protocol.py` into the same folder on your Linux VPS
(or clone this repository), then run:

```sh
bash vps.sh start --port 5000
bash vps.sh status
bash vps.sh stop
```

The relay listens on `0.0.0.0`. Allow inbound **TCP port 5000** in your VPS
provider's firewall and the server firewall. The script runs it in the
background and keeps it running after you disconnect from SSH. No root access
is needed. It requires Bash and `flock` (usually provided by `util-linux`).

Use these commands to restart with different options and follow the log:

```sh
bash vps.sh restart --host 0.0.0.0 --port 5000
tail -f .run/relay.log
```

`start` defaults to port 5000 and does nothing if the managed relay is already
running. Pass your options again on each restart; they are not saved. Logs
append to `.run/relay.log`; the PID and lock also live in `.run/`, which Git
ignores. Use the same Linux account for all commands. The script can be invoked
from any directory and manages only the process it launched. It does not
configure automatic startup after a VPS reboot. Set `PYTHON=/path/to/python3`
if you need a particular interpreter. `status` returns exit code 1 if stopped.

For foreground operation, `python3 relay.py --port 5000` is still available;
stop that process with Ctrl+C rather than `vps.sh stop`.

As requested, this prototype has no authentication or encryption. Anyone who
can reach its listening port can publish or subscribe to a mouse stream.

## 2. Desktop (source)

Keep `source.py`, `winmouse.py`, and `protocol.py` in the same folder:

```powershell
python source.py
```

Choose a numbered local mouse, then enter the VPS IP and port:

```text
Available local mouse devices:
[1] HID-compliant mouse
    \\?\HID#VID_... (device path distinguishes identical names)
Select mouse: 1
VPS IP address: 203.0.113.10
VPS port [5000]: 5000
```

Use your real VPS address; the address above is an example. Only events from
the selected device are forwarded. The selected mouse **also continues to
control the Desktop locally**. Release its buttons before starting capture.

## 3. Surface (destination)

Keep `destination.py`, `winmouse.py`, and `protocol.py` in the same folder:

```powershell
python destination.py
```

```text
VPS IP address: 203.0.113.10
VPS port [5000]: 5000
Available remote mice:
[1] DESKTOP / HID-compliant mouse
Select mouse: 1
Connected to DESKTOP / HID-compliant mouse.
```

Start the source before selecting it on the Surface. For repeated use, either
Windows client also accepts `--host YOUR_VPS_IP --port 5000`; mouse selection
remains interactive. Multiple sources and destinations are supported.

## Stopping and reconnecting

Press **Ctrl+Alt+F12** on the respective Windows computer, or **Ctrl+C** in its
console. The destination releases remotely held buttons when it exits, loses
the connection, or receives a source-disconnected message. An interrupted
network connection is detected after approximately six seconds of inactivity;
if only the source loses connectivity, allow additional relay delivery time.
Rerun a client to reconnect or refresh the device list. Unplugging the chosen
source mouse stops sharing; select it again after reconnecting it.

## Behavior and limits

- Input is streamed as events, not polled from a latest-state variable.
  Each event carries the full button state, and a new subscriber receives
  the current button state immediately.
- TCP preserves order. Network latency and retransmissions can still cause
  lag. A full application queue disconnects rather than dropping button
  changes or retaining an unbounded backlog.
- Source and destination pointer speed settings may make movement feel
  different. Absolute mouse coordinates are supported, but this is not
  screen sharing or cursor-position synchronization.
- Windows may block SendInput into elevated applications. If that is needed,
  run the destination at the corresponding privilege level. UAC secure
  desktop/login control and hardware-only input consumers are outside scope.
- USB receivers, touchpads, and virtual input devices may expose generic names
  or combine physical devices. Selection follows the devices Windows exposes
  through Raw Input; the full device path is printed to distinguish them.
- Avoid subscribing on the source computer itself; this prototype is intended
  for two separate Windows computers.

## Validation

```sh
python -m unittest discover -s tests -v
```

The tests exercise real local TCP relay connections, source selection,
multiple subscribers, button synchronization, disconnects, invalid input,
queue overflow, and Windows input translation with injection mocked out.
Actual mouse behavior across your VPS and two PCs needs a hardware check:
move each mouse to verify selection, test clicks/drag/scroll, stop the source
while holding a button, and check the local stop shortcut on the destination.
