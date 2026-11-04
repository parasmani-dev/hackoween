"""Exercise the real bounded PNG writer without Webots or provider transport."""

import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        source = Path(__file__).parent / "controllers/bridge/bridge.py"
        spec = importlib.util.spec_from_file_location("capture_test_bridge", source)
        self.bridge = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"controller": SimpleNamespace(Supervisor=Mock())}):
            spec.loader.exec_module(self.bridge)
        self.bridge.__file__ = str(Path(self.temp.name) / "controllers/bridge/bridge.py")
        self.scene = self.bridge.Scene.__new__(self.bridge.Scene)
        self.scene.capture = None
        self.scene.drive = Mock()
        self.scene.drive.robot.getTime.return_value = 0
        self.scene.camera = Mock()
        self.scene.camera.getWidth.return_value = 1280
        self.scene.camera.getHeight.return_value = 960
        self.scene.camera.getImage.return_value = bytes([12, 34, 56, 255]) * (1280 * 960)

    def test_flush_preserves_every_native_frame_and_bounds_queue(self):
        capture_id = self.scene.start_capture("Offline fixture")
        for index in range(1, 20):
            self.scene.drive.robot.getTime.return_value = index * 0.032
            self.scene.record_frame()
            self.assertLessEqual(len(self.scene.pending), 8)
        path = Path(self.scene.finish_capture())
        manifest = json.loads(path.read_text())
        self.assertEqual(manifest["run_id"], capture_id)
        self.assertEqual(len(manifest["frames"]), 20)
        for frame in manifest["frames"]:
            with Image.open(path.parent / frame["path"]) as image:
                image.load()
                self.assertEqual(image.size, (1280, 960))
                self.assertEqual(image.getpixel((0, 0)), (56, 34, 12))
        self.assertIsNone(self.scene.finish_capture())

    def test_writer_error_never_publishes_complete_manifest(self):
        with patch.object(self.scene, "save_frame", side_effect=OSError("disk failure")):
            self.scene.start_capture("Offline failure fixture")
            root = self.scene.capture[0]
            with self.assertRaises(OSError):
                self.scene.finish_capture()
        self.assertFalse((root / "capture.json").exists())


if __name__ == "__main__":
    unittest.main()
