"""Adapters for installed USB/IP drivers/tools; never synthesize mouse input."""
import json
import os
from pathlib import Path
import shutil
import subprocess

from protocol import bus_id


def tool(name, override=None):
    if override:
        found = shutil.which(override)
        if found:
            return found
        raise ValueError(f"Executable not found: {override}")
    found = shutil.which(name)
    if found:
        return found
    folder = "usbipd-win" if name == "usbipd" else "USBip"
    candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / folder / (name + ".exe")
    if candidate.is_file():
        return str(candidate)
    project = "dorssel/usbipd-win" if name == "usbipd" else "vadimgrn/usbip-win2"
    raise ValueError(f"{name} is not installed. Install the Windows drivers/tools from {project}; see README.md.")


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8-sig",
                            errors="replace", timeout=30)
    if result.returncode:
        raise OSError((result.stderr or result.stdout).strip() or f"Command failed: {args[0]}")
    return result.stdout


def devices_from_state(state, mouse_ancestors, all_usb=False):
    """Match Mouse PnP children to exact USB ancestors, never just VID/PID."""
    result = []
    ancestors = {value.upper() for value in mouse_ancestors}
    for item in state.get("Devices", []):
        instance = item.get("InstanceId", "")
        if not item.get("BusId") or (not all_usb and instance.upper() not in ancestors):
            continue
        result.append(dict(name=item.get("Description") or "USB device",
                           busid=bus_id(item["BusId"]), path=instance,
                           shared=bool(item.get("PersistedGuid")),
                           attached=bool(item.get("ClientIPAddress"))))
    return result


def list_devices(usbipd, all_usb=False):
    state = json.loads(command(usbipd, "state"))
    ancestors = []
    if not all_usb:
        # A mouse may be a child of a composite USB receiver.
        script = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ancestors = @()
foreach ($mouse in @(Get-PnpDevice -Class Mouse -PresentOnly -ErrorAction SilentlyContinue)) {
    $deviceId = $mouse.InstanceId
    for ($depth = 0; $depth -lt 16 -and $deviceId; $depth++) {
        $ancestors += $deviceId
        $parent = Get-PnpDeviceProperty -InstanceId $deviceId -KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue
        $deviceId = $parent.Data
    }
}
ConvertTo-Json -InputObject @($ancestors | Select-Object -Unique) -Compress
"""
        ancestors = json.loads(command("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script))
    return devices_from_state(state, ancestors, all_usb)


def require_windows():
    if os.name != "nt":
        raise ValueError("This client requires Windows with the USB/IP driver installed")
