# Physical USB mouse forwarding through a VPS

This version forwards a **real USB device using USB/IP**. The Desktop's USB
driver exports the selected physical mouse or receiver, and the Surface's
virtual USB controller attaches that device to Windows. Python carries USB/IP
traffic through the Linux VPS. There is no Raw Input capture or SendInput replay.

```text
Desktop mouse -> usbipd-win -> source.py -> VPS relay
                                             |
Surface USB controller <- usbip-win2 <- destination.py
```

Both PCs connect outward to the VPS. One destination can attach a selected
device at a time. The Desktop loses use of the device while it is attached
remotely. A combined mouse/keyboard receiver is forwarded as a **whole USB
device**, including its other functions.

Physical USB mice and USB wireless receivers are supported by this design.
A Bluetooth mouse or I2C touchpad is not an independently exportable USB mouse.

## Requirements

- Python 3.10+ on all machines; no third-party Python packages.
- Desktop: install [usbipd-win](https://github.com/dorssel/usbipd-win/releases),
  including its Windows service and driver.
- Surface: install [usbip-win2](https://github.com/vadimgrn/usbip-win2/releases),
  including its virtual USB controller driver and `usbip.exe`. Choose the
  upstream installer for your Windows architecture and follow its restart
  instructions.
- Run the Windows clients from an Administrator terminal for driver operations.
- VPS launcher: Linux, Bash, and `flock` from util-linux.

The scripts do not install drivers or change Windows signing settings. Keep a
separate local keyboard/touchpad available to stop sharing.

**Update the VPS and both Windows clients together.** The old cursor-replay
version is incompatible with this USB/IP protocol.

## 1. Linux VPS

Clone the repository, or copy `vps.sh`, `relay.py`, and `protocol.py` into one
folder. Allow inbound TCP port 5000 in the VPS/provider firewall. Run:

```bash
bash vps.sh start --port 5000
bash vps.sh status
bash vps.sh stop
```

For an existing checkout, update and restart:

```bash
git pull
bash vps.sh restart --port 5000
tail -f .run/relay.log
```

The default listen address is `0.0.0.0`; override it with `--host` on start or
restart. Custom options must be passed again on restart. The relay runs after
SSH disconnects but does not automatically start after a VPS reboot. Use the
same Linux account for start/stop. PID, lock, and appended log files live under
`.run/`, excluded from Git. `status` exits with code 1 when stopped.
Set `PYTHON=/path/to/python3` to choose an interpreter.

Foreground operation is also available with `python3 relay.py --port 5000`;
stop it with Ctrl+C. The launcher manages only the process it started.

As requested, there is no authentication or encryption. Anyone who can reach
the relay can discover and attempt to attach advertised USB devices.

## 2. Desktop: choose the physical mouse

Keep the complete repository on the Desktop, or at least `source.py`,
`protocol.py`, `usb_backend.py`, and `usb_tunnel.py`. After installing usbipd-win,
open Administrator PowerShell:

```powershell
python source.py
```

```text
Available physical USB mice/receivers:
[1] USB Optical Mouse (USB bus 1-3)
    USB\VID_...\...
Select device: 1
VPS IP address: YOUR_VPS_IP
VPS port [5000]: 5000
```

Mouse PnP devices are matched to their exact physical USB ancestors. The bus
ID and instance path distinguish identical mice. The program binds only the
selected device with usbipd and registers it at the VPS.

If a receiver is not identified as a mouse:

```powershell
python source.py --all-usb
```

If usbipd says the device needs forced binding, stop sharing, unbind the device
if already shared, then opt into forced binding:

```powershell
usbipd unbind --busid 1-3
python source.py --all-usb --force
```

Replace `1-3` with the selected device's bus ID. Forced binding replaces its
local driver and may make it unavailable locally before a destination attaches.
Already-shared devices are otherwise left bound. The program only undoes
bindings created during its current run.

usbipd must be running locally on port 3240. No router port forwarding or VPS
port 3240 is needed. The source connects to usbipd on loopback and permits
imports only for the selected bus ID.

Optional arguments:

```powershell
python source.py --host YOUR_VPS_IP --port 5000
python source.py --usbipd "C:\Program Files\usbipd-win\usbipd.exe"
```

## 3. Surface: attach the USB device

Keep the complete repository on the Surface, or `destination.py`, `protocol.py`,
and `usb_backend.py`. After installing usbip-win2, open Administrator PowerShell:

```powershell
python destination.py
```

```text
VPS IP address: YOUR_VPS_IP
VPS port [5000]: 5000
Available remote physical USB devices:
[1] DESKTOP / USB Optical Mouse (USB bus 1-3)
Select device: 1
Physical USB device attached on Windows USB/IP port 1: DESKTOP / USB Optical Mouse
```

The program creates a loopback proxy and invokes `usbip attach`. Windows
attaches the device through the installed USB/IP controller driver; Python
forwards its USB/IP traffic. Its HID functions should enumerate in Windows
instead of being simulated by this application.

Optional arguments:

```powershell
python destination.py --host YOUR_VPS_IP --port 5000
python destination.py --usbip "C:\Program Files\USBip\usbip.exe"
```

Start the Desktop source first. Busy devices are omitted from selection. Keep
both clients running while using the device.

## Stop and reconnect

Press **Ctrl+C in either client's terminal**. There is no global Ctrl+Alt+F12
shortcut in this version. The destination closes its tunnel and detaches its
specific USB/IP port. The source closes its channels and undoes sharing only
if it created the binding.

A crash or broken network may require manual cleanup:

```powershell
# Surface: inspect ports, then detach only the affected port.
usbip port
usbip detach -p 1

# Desktop: undo sharing for the chosen bus ID if needed.
usbipd unbind --busid 1-3
```

Use your actual port/bus ID. Rerun clients to reconnect or refresh their lists.
Control heartbeats detect a silent source connection after roughly ten seconds;
native driver recovery time may vary.

## Troubleshooting a source connection error

The source checks the VPS connection and protocol before binding a USB device.
A connection timeout means the TCP connection did not complete; check the VPS
listen address, server/provider firewall, and the route from the Desktop.
The VPS must listen on a reachable interface, not only `127.0.0.1`.

Run these inside the VPS repository:

```bash
bash vps.sh status
ss -ltnp 'sport = :5000'
tail -n 30 .run/relay.log
```

If the relay is stopped or using an old version:

```bash
git pull
bash vps.sh restart --host 0.0.0.0 --port 5000
```

Allow inbound TCP 5000 in both the VPS provider firewall and any enabled Linux
firewall. For a full source traceback, run `python source.py --debug`.
Older clients could show a blank `Source error:` on timeout; update the Desktop
checkout with `git pull` to get the descriptive error message.

## Validation status

This USB/IP revision has not been verified with physical hardware. Simulated
device tests were intentionally skipped; hardware validation is left to the
user. Obsolete event-replay tests were removed with that implementation.
The remaining `tests/test_vps_script.py` checks only the Linux launcher's
lifecycle, not USB driver behavior or mouse attachment.
