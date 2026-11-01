"""Lifecycle checks for the VPS launcher; run these on Linux."""
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "linux" and shutil.which("bash") and shutil.which("flock"),
                     "VPS lifecycle tests require Linux, Bash, and flock")
class VpsScriptTests(unittest.TestCase):
    def setUp(self):
        runtime = ROOT / ".run"
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="test launcher ", dir=runtime)
        self.project = Path(self.temp.name)
        for name in ("vps.sh", "relay.py", "protocol.py"):
            shutil.copyfile(ROOT / name, self.project / name)

    def command(self, *args):
        import os
        return subprocess.run(["bash", str(self.project / "vps.sh"), *args],
                              cwd=ROOT, capture_output=True, text=True, timeout=15,
                              env={**os.environ, "PYTHON": sys.executable})

    def tearDown(self):
        self.command("stop")
        self.temp.cleanup()

    def test_start_duplicate_restart_and_stop(self):
        self.assertEqual(self.command("status").returncode, 1)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = str(probe.getsockname()[1])
        result = self.command("start", "--host", "127.0.0.1", "--port", port)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with socket.create_connection(("127.0.0.1", int(port)), timeout=2):
            pass
        pid_file = self.project / ".run" / "relay.pid"
        original = pid_file.read_text()
        self.assertEqual(self.command("start").returncode, 0)
        self.assertEqual(pid_file.read_text(), original)
        self.assertEqual(self.command("status").returncode, 0)
        result = self.command("restart", "--host", "127.0.0.1", "--port", port)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(pid_file.read_text(), original)
        self.assertEqual(self.command("stop").returncode, 0)
        self.assertFalse(pid_file.exists())
        self.assertEqual(self.command("stop").returncode, 0)
        self.assertEqual(self.command("status").returncode, 1)

    def test_bind_failure_is_reported(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            result = self.command("start", "--host", "127.0.0.1", "--port",
                                  str(listener.getsockname()[1]))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed to start", result.stderr)
        self.assertEqual(self.command("status").returncode, 1)

    def test_stale_identity_does_not_stop_another_process(self):
        with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]) as unrelated:
            try:
                state = self.project / ".run"
                state.mkdir()
                (state / "relay.pid").write_text(f"{unrelated.pid} 0\n")
                self.assertEqual(self.command("stop").returncode, 0)
                self.assertIsNone(unrelated.poll())
            finally:
                unrelated.terminate()
                unrelated.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
