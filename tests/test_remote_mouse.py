import asyncio
import os
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from protocol import decode, encode, mouse_event, state_event
from relay import Relay, Source


class ProtocolTests(unittest.TestCase):
    def test_roundtrip_and_validation(self):
        event = state_event(17)
        event.update(dx=-30, dy=11, wheel=-120)
        self.assertEqual(mouse_event(decode(encode(event))), event)
        for field, value in (("buttons", 32), ("dx", True), ("wheel", 99999), ("absolute", 1)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                mouse_event({**event, field: value})

    def test_absolute_coordinates(self):
        with self.assertRaises(ValueError):
            mouse_event({**state_event(), "absolute": True, "dx": -1})

    def test_slow_destination_is_disconnected_without_silent_loss(self):
        relay = Relay()
        source = Source("test")
        queue = asyncio.Queue(maxsize=1)
        source.listeners.add(queue)
        relay.publish(source, state_event(1))
        relay.publish(source, state_event())
        self.assertEqual(queue.get_nowait()["type"], "error")
        self.assertNotIn(queue, source.listeners)


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.relay = Relay()
        self.tasks = set()

        def connected(reader, writer):
            task = asyncio.create_task(self.relay.handle(reader, writer))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

        self.server = await asyncio.start_server(connected, "127.0.0.1", 0, limit=4096)
        self.port = self.server.sockets[0].getsockname()[1]
        self.writers = []

    async def asyncTearDown(self):
        for writer in self.writers:
            writer.close()
        for writer in self.writers:
            await writer.wait_closed()
        self.server.close()
        await self.server.wait_closed()
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*tuple(self.tasks), return_exceptions=True)

    async def client(self, hello):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        self.writers.append(writer)
        writer.write(encode(hello))
        await writer.drain()
        return reader, writer, await self.read(reader)

    async def read(self, reader):
        return decode(await asyncio.wait_for(reader.readline(), 2))

    async def source(self, name):
        return await self.client({"type": "source", "name": name})

    async def subscribe(self, source_id):
        reader, writer, listing = await self.client({"type": "destination"})
        writer.write(encode({"type": "subscribe", "id": source_id}))
        await writer.drain()
        self.assertEqual((await self.read(reader))["type"], "subscribed")
        initial = await self.read(reader)
        return reader, writer, listing, initial

    async def test_listing_selection_input_and_disconnect(self):
        _, source1, first = await self.source("Desktop / mouse one")
        _, source2, second = await self.source("Desktop / mouse two")
        dest, _, listing, initial = await self.subscribe(second["id"])
        self.assertEqual(len(listing["sources"]), 2)
        self.assertEqual(initial, state_event())
        source1.write(encode({**state_event(1), "dx": 99}))
        wanted = {**state_event(2), "dx": -7, "wheel": 120}
        source2.write(encode(wanted))
        await source2.drain()
        self.assertEqual(await self.read(dest), wanted)
        source2.close()
        await source2.wait_closed()
        self.assertEqual((await self.read(dest))["type"], "offline")

    async def test_multiple_destinations_and_button_sync(self):
        _, source, info = await self.source("mouse")
        source.write(encode(state_event(9)))
        await source.drain()
        for _ in range(50):
            if self.relay.sources[info["id"]].buttons == 9:
                break
            await asyncio.sleep(0.01)
        one, _, _, initial = await self.subscribe(info["id"])
        self.assertEqual(initial["buttons"], 9)
        two, _, _, _ = await self.subscribe(info["id"])
        source.write(encode(state_event()))
        await source.drain()
        self.assertEqual(await self.read(one), state_event())
        self.assertEqual(await self.read(two), state_event())

    async def test_invalid_input_removes_source(self):
        source_reader, source, info = await self.source("mouse")
        dest, _, _, _ = await self.subscribe(info["id"])
        source.write(encode({**state_event(), "buttons": 99}))
        await source.drain()
        with self.assertRaises(ConnectionError):
            await self.read(source_reader)
        self.assertEqual((await self.read(dest))["type"], "offline")
        self.assertNotIn(info["id"], self.relay.sources)

    async def test_empty_list_and_stale_selection(self):
        reader, writer, listing = await self.client({"type": "destination"})
        self.assertEqual(listing["sources"], [])
        writer.write(encode({"type": "subscribe", "id": "gone"}))
        await writer.drain()
        with self.assertRaises(ConnectionError):
            await self.read(reader)

    async def test_unresponsive_source_is_removed(self):
        _, _, info = await self.source("idle mouse")
        dest, _, _, _ = await self.subscribe(info["id"])
        # The read timeout is bound when Relay.read is defined; speed up
        # asyncio.wait_for only for the source's next read.
        original_read = self.relay.read

        async def short_read(reader, timeout=0.05):
            return await original_read(reader, timeout)

        self.relay.read = short_read
        source_writer = self.writers[0]
        source_writer.write(encode({"type": "ping"}))
        await source_writer.drain()
        self.assertEqual((await self.read(dest))["type"], "offline")
        self.assertNotIn(info["id"], self.relay.sources)


@unittest.skipUnless(os.name == "nt", "Windows input translation")
class InputTests(unittest.TestCase):
    def test_destination_releases_buttons_after_disconnect(self):
        import destination
        from unittest.mock import Mock
        applied = threading.Event()
        messages = iter([
            {"type": "sources", "sources": [{"id": "one", "name": "mouse"}]},
            {"type": "subscribed"}, state_event(1)])

        def receive():
            message = next(messages, None)
            if message is not None:
                return message
            if not applied.wait(2):
                raise ConnectionError("Timed out waiting for input application")
            raise ConnectionError("Source disconnected")

        connection = Mock()
        connection.receive.side_effect = receive
        injector = Mock()
        injector.apply.side_effect = lambda event: applied.set()
        with patch.object(destination, "Connection", return_value=connection), \
             patch.object(destination, "choose", side_effect=lambda items, label: items[0]), \
             patch("winmouse.Injector", return_value=injector), \
             patch("winmouse.stop_pressed", return_value=False):
            with self.assertRaisesRegex(ConnectionError, "Source disconnected"):
                destination.run(SimpleNamespace(host="127.0.0.1", port=5000))
        injector.apply.assert_called_once_with(state_event(1))
        injector.release.assert_called_once()
        connection.close.assert_called_once()

    def test_buttons_wheels_and_cleanup_without_real_injection(self):
        from winmouse import Injector
        injector = Injector()
        with patch.object(injector, "send") as send:
            injector.apply({**state_event(31), "dx": 12, "dy": -3, "wheel": -120, "hwheel": 120})
            self.assertEqual(send.call_args_list[0].args, (1, 12, -3))
            self.assertEqual(send.call_count, 8)
            send.reset_mock()
            injector.release()
            self.assertEqual(send.call_count, 5)
            self.assertEqual(injector.buttons, 0)
            injector.release()
            self.assertEqual(send.call_count, 5)

    def test_absolute_virtual_desktop_flags(self):
        from winmouse import Injector
        injector = Injector()
        with patch.object(injector, "send") as send:
            injector.apply({**state_event(), "absolute": True, "virtual": True, "dx": 65535})
            send.assert_called_once_with(0xC001, 65535, 0)

    def test_native_structure_sizes(self):
        import ctypes
        from winmouse import Input, MousePacket
        self.assertEqual(ctypes.sizeof(Input), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        self.assertEqual(ctypes.sizeof(MousePacket), 48 if ctypes.sizeof(ctypes.c_void_p) == 8 else 40)


if __name__ == "__main__":
    unittest.main()
