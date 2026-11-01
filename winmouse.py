"""Windows Raw Input capture and SendInput replay (no third-party packages)."""
import ctypes as C
from ctypes import wintypes as W
import os

if os.name != "nt":
    raise ImportError("Mouse capture and replay require Windows")

user32 = C.WinDLL("user32", use_last_error=True)
kernel32 = C.WinDLL("kernel32", use_last_error=True)
ULONG_PTR = C.c_size_t
LRESULT = C.c_ssize_t
WNDPROC = C.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)


class DeviceList(C.Structure):
    _fields_ = [("handle", W.HANDLE), ("kind", W.DWORD)]


class RawDevice(C.Structure):
    _fields_ = [("page", W.USHORT), ("usage", W.USHORT),
                ("flags", W.DWORD), ("target", W.HWND)]


class RawHeader(C.Structure):
    _fields_ = [("kind", W.DWORD), ("size", W.DWORD),
                ("device", W.HANDLE), ("param", W.WPARAM)]


class ButtonPair(C.Structure):
    _fields_ = [("flags", W.USHORT), ("data", W.USHORT)]


class ButtonUnion(C.Union):
    _fields_ = [("pair", ButtonPair), ("all", W.ULONG)]


class RawMouse(C.Structure):
    _fields_ = [("flags", W.USHORT), ("buttons", ButtonUnion),
                ("raw_buttons", W.ULONG), ("x", W.LONG), ("y", W.LONG),
                ("extra", W.ULONG)]


class MousePacket(C.Structure):
    _fields_ = [("header", RawHeader), ("mouse", RawMouse)]


class WindowClass(C.Structure):
    _fields_ = [("style", W.UINT), ("proc", WNDPROC), ("class_extra", C.c_int),
                ("window_extra", C.c_int), ("instance", W.HINSTANCE),
                ("icon", W.HICON), ("cursor", W.HANDLE), ("background", W.HBRUSH),
                ("menu", W.LPCWSTR), ("name", W.LPCWSTR)]


class MouseInput(C.Structure):
    _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("data", W.DWORD),
                ("flags", W.DWORD), ("time", W.DWORD), ("extra", ULONG_PTR)]


class KeyboardInput(C.Structure):
    _fields_ = [("vk", W.WORD), ("scan", W.WORD), ("flags", W.DWORD),
                ("time", W.DWORD), ("extra", ULONG_PTR)]


class HardwareInput(C.Structure):
    _fields_ = [("message", W.DWORD), ("low", W.WORD), ("high", W.WORD)]


class InputUnion(C.Union):
    _fields_ = [("mouse", MouseInput), ("keyboard", KeyboardInput), ("hardware", HardwareInput)]


class Input(C.Structure):
    _fields_ = [("kind", W.DWORD), ("value", InputUnion)]


def signature(dll, name, result, *args):
    function = getattr(dll, name)
    function.restype = result
    function.argtypes = args
    return function


signature(user32, "GetRawInputDeviceList", W.UINT, C.POINTER(DeviceList), C.POINTER(W.UINT), W.UINT)
signature(user32, "GetRawInputDeviceInfoW", W.UINT, W.HANDLE, W.UINT, C.c_void_p, C.POINTER(W.UINT))
signature(user32, "GetRawInputData", W.UINT, W.HANDLE, W.UINT, C.c_void_p, C.POINTER(W.UINT), W.UINT)
signature(user32, "RegisterRawInputDevices", W.BOOL, C.POINTER(RawDevice), W.UINT, W.UINT)
signature(user32, "RegisterClassW", W.ATOM, C.POINTER(WindowClass))
signature(user32, "UnregisterClassW", W.BOOL, W.LPCWSTR, W.HINSTANCE)
signature(user32, "CreateWindowExW", W.HWND, W.DWORD, W.LPCWSTR, W.LPCWSTR,
          W.DWORD, C.c_int, C.c_int, C.c_int, C.c_int, W.HWND, W.HMENU, W.HINSTANCE, C.c_void_p)
signature(user32, "DefWindowProcW", LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
signature(user32, "DestroyWindow", W.BOOL, W.HWND)
signature(user32, "PeekMessageW", W.BOOL, C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT)
signature(user32, "TranslateMessage", W.BOOL, C.POINTER(W.MSG))
signature(user32, "DispatchMessageW", LRESULT, C.POINTER(W.MSG))
signature(user32, "RegisterHotKey", W.BOOL, W.HWND, C.c_int, W.UINT, W.UINT)
signature(user32, "UnregisterHotKey", W.BOOL, W.HWND, C.c_int)
signature(user32, "SendInput", W.UINT, W.UINT, C.POINTER(Input), C.c_int)
signature(user32, "GetAsyncKeyState", W.SHORT, C.c_int)
signature(kernel32, "GetModuleHandleW", W.HMODULE, W.LPCWSTR)


def checked(value):
    if not value:
        raise C.WinError(C.get_last_error())
    return value


def friendly_name(path):
    """Best-effort registry display name; the full device path remains visible."""
    import winreg
    parts = path.removeprefix("\\\\?\\").split("#")
    if len(parts) >= 3:
        try:
            key_path = "SYSTEM\\CurrentControlSet\\Enum\\" + "\\".join(parts[:3])
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                for field in ("FriendlyName", "DeviceDesc"):
                    try:
                        return str(winreg.QueryValueEx(key, field)[0]).split(";")[-1]
                    except OSError:
                        pass
        except OSError:
            pass
    return "HID mouse"


def list_mice():
    # Devices can appear between the count and enumeration calls.
    for _ in range(3):
        count = W.UINT()
        if user32.GetRawInputDeviceList(None, C.byref(count), C.sizeof(DeviceList)) == 0xFFFFFFFF:
            raise C.WinError(C.get_last_error())
        devices = (DeviceList * count.value)()
        found = user32.GetRawInputDeviceList(devices, C.byref(count), C.sizeof(DeviceList))
        if found != 0xFFFFFFFF:
            break
    else:
        raise C.WinError(C.get_last_error())
    result = []
    for device in devices[:found]:
        if device.kind != 0:  # RIM_TYPEMOUSE
            continue
        size = W.UINT()
        if user32.GetRawInputDeviceInfoW(device.handle, 0x20000007, None, C.byref(size)) == 0xFFFFFFFF:
            continue
        name = C.create_unicode_buffer(size.value + 1)
        size.value = len(name)
        if user32.GetRawInputDeviceInfoW(device.handle, 0x20000007, name, C.byref(size)) == 0xFFFFFFFF:
            continue
        result.append(dict(handle=device.handle, name=friendly_name(name.value), path=name.value))
    return result


def capture(handle, emit, stop):
    """Run a hidden message window; forward only the chosen hDevice."""
    buttons = 0
    errors = []

    @WNDPROC
    def window_proc(hwnd, message, wparam, lparam):
        nonlocal buttons
        try:
            if message == 0x0312:  # WM_HOTKEY
                stop.set()
            elif message == 0x00FE and wparam == 2 and lparam == handle:
                raise ConnectionError("Selected mouse was unplugged")
            elif message == 0x00FF:  # WM_INPUT
                size = W.UINT()
                if user32.GetRawInputData(lparam, 0x10000003, None, C.byref(size), C.sizeof(RawHeader)) == 0xFFFFFFFF:
                    raise C.WinError(C.get_last_error())
                buffer = C.create_string_buffer(size.value)
                received = user32.GetRawInputData(lparam, 0x10000003, buffer, C.byref(size), C.sizeof(RawHeader))
                if received == 0xFFFFFFFF:
                    raise C.WinError(C.get_last_error())
                if received >= C.sizeof(MousePacket):
                    packet = MousePacket.from_buffer(buffer)
                    if packet.header.kind == 0 and packet.header.device == handle:
                        mouse = packet.mouse
                        flags = mouse.buttons.pair.flags
                        for index in range(5):
                            if flags & (1 << (index * 2)):
                                buttons |= 1 << index
                            if flags & (2 << (index * 2)):
                                buttons &= ~(1 << index)
                        wheel = C.c_short(mouse.buttons.pair.data).value
                        emit(dict(type="mouse", dx=mouse.x, dy=mouse.y, buttons=buttons,
                                  wheel=wheel if flags & 0x0400 else 0,
                                  hwheel=wheel if flags & 0x0800 else 0,
                                  absolute=bool(mouse.flags & 1), virtual=bool(mouse.flags & 2)))
        except Exception as exc:
            errors.append(exc)
            stop.set()
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    instance = kernel32.GetModuleHandleW(None)
    class_name = f"RemoteMouseCapture_{os.getpid()}"
    window_class = WindowClass(proc=window_proc, instance=instance, name=class_name)
    checked(user32.RegisterClassW(C.byref(window_class)))
    hwnd = None
    registered = False
    try:
        hwnd = checked(user32.CreateWindowExW(0, class_name, "", 0, 0, 0, 0, 0,
                                             W.HWND(-3), None, instance, None))
        # RIDEV_INPUTSINK | RIDEV_DEVNOTIFY: receive input while console is unfocused.
        raw_device = RawDevice(1, 2, 0x2100, hwnd)
        checked(user32.RegisterRawInputDevices(C.byref(raw_device), 1, C.sizeof(raw_device)))
        registered = True
        checked(user32.RegisterHotKey(hwnd, 1, 0x4003, 0x7B))  # Ctrl+Alt+F12
        message = W.MSG()
        while not stop.is_set():
            while user32.PeekMessageW(C.byref(message), None, 0, 0, 1):
                user32.TranslateMessage(C.byref(message))
                user32.DispatchMessageW(C.byref(message))
                if stop.is_set():
                    break
            stop.wait(0.002)
        if errors:
            raise errors[0]
    finally:
        if registered:
            remove = RawDevice(1, 2, 1, None)
            user32.RegisterRawInputDevices(C.byref(remove), 1, C.sizeof(remove))
        if hwnd:
            user32.UnregisterHotKey(hwnd, 1)
            user32.DestroyWindow(hwnd)
        user32.UnregisterClassW(class_name, instance)


def stop_pressed():
    return all(user32.GetAsyncKeyState(key) & 0x8000 for key in (0x11, 0x12, 0x7B))


class Injector:
    def __init__(self):
        self.buttons = 0

    def send(self, flags, dx=0, dy=0, data=0):
        packet = Input(0, InputUnion(mouse=MouseInput(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)))
        if user32.SendInput(1, C.byref(packet), C.sizeof(Input)) != 1:
            raise OSError("SendInput failed; Windows may be blocking input to an elevated application")

    def apply(self, event):
        if event["dx"] or event["dy"] or event["absolute"]:
            flags = 0x0001
            if event["absolute"]:
                flags |= 0x8000
                if event["virtual"]:
                    flags |= 0x4000
            self.send(flags, event["dx"], event["dy"])
        # Raw Input button order is left, right, middle, X1, X2.
        for index, down, up, data in ((0, 2, 4, 0), (1, 8, 16, 0),
                                      (2, 32, 64, 0), (3, 128, 256, 1), (4, 128, 256, 2)):
            mask = 1 << index
            if (self.buttons ^ event["buttons"]) & mask:
                self.send(down if event["buttons"] & mask else up, data=data)
                self.buttons = (self.buttons & ~mask) | (event["buttons"] & mask)
        if event["wheel"]:
            self.send(0x0800, data=event["wheel"])
        if event["hwheel"]:
            self.send(0x1000, data=event["hwheel"])

    def release(self):
        from protocol import state_event
        self.apply(state_event())
