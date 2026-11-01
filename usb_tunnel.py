"""USB/IP import gate: only the selected physical USB device is exported."""
import asyncio
import struct

from protocol import bridge, close, connect

USBIP_VERSION = 0x0111
IMPORT = 0x8003
DEVLIST = 0x8005
HEADER = struct.Struct("!HHI")
DEVICE_SIZE = 312


async def read_exact(reader, size):
    return await asyncio.wait_for(reader.readexactly(size), 10)


async def forward_device(reader, writer, selected_busid, usb_host="127.0.0.1", usb_port=3240):
    """Validate the opening request; transfer native URBs unchanged afterward."""
    header = await read_exact(reader, HEADER.size)
    version, operation, status = HEADER.unpack(header)
    if version != USBIP_VERSION or status != 0 or operation not in (IMPORT, DEVLIST):
        raise ValueError("Unsupported USB/IP handshake")
    request = header
    if operation == IMPORT:
        bus = await read_exact(reader, 32)
        if bus != selected_busid.encode("ascii").ljust(32, b"\0"):
            writer.write(HEADER.pack(USBIP_VERSION, IMPORT & 0x7FFF, 1))
            await writer.drain()
            return
        request += bus
    local_reader, local_writer = await connect(usb_host, usb_port)
    try:
        local_writer.write(request)
        await local_writer.drain()
        if operation == IMPORT:
            await bridge(reader, writer, local_reader, local_writer)
        else:
            # Hide any other devices already shared by usbipd.
            reply = await read_exact(local_reader, 12)
            reply_version, reply_op, reply_status, count = struct.unpack("!HHII", reply)
            if (reply_version, reply_op, reply_status) != (USBIP_VERSION, 5, 0) or count > 256:
                raise ValueError("Invalid USB/IP device list")
            selected = []
            for _ in range(count):
                descriptor = await read_exact(local_reader, DEVICE_SIZE)
                interfaces = await read_exact(local_reader, descriptor[-1] * 4)
                if descriptor[256:288] == selected_busid.encode("ascii").ljust(32, b"\0"):
                    selected.append(descriptor + interfaces)
            writer.write(struct.pack("!HHII", USBIP_VERSION, 5, 0, len(selected)) + b"".join(selected))
            await writer.drain()
    finally:
        await close(local_writer)
