"""Optional preview must not fail startup/reset or physical actions."""
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from PIL import Image


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        spec = importlib.util.spec_from_file_location('preview_test_bridge', Path(__file__).parent / 'controllers/bridge/bridge.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'controller': SimpleNamespace(Supervisor=Mock())}):
            spec.loader.exec_module(module)
        self.scene = module.Scene.__new__(module.Scene)
        self.scene.preview_path = Path(self.temp.name) / 'preview.jpg'
        self.scene.preview_session = 'new-session'
        self.scene.preview_ready = False
        self.scene.camera_pixels = Mock(side_effect=ValueError('NULL pointer access'))
        robot = Mock()
        robot.getBasicTimeStep.return_value = 32
        robot.step.return_value = 0
        robot.getSelf.return_value.getOrientation.return_value = [1,0,0,0,1,0,0,0,1]
        robot.getSelf.return_value.getPosition.return_value = [0,0,0]
        robot.getSelf.return_value.getContactPoints.return_value = []
        self.scene.drive = module.RoverDrive(robot)
        self.scene.drive.preview_step = self.scene.publish_preview

    def test_missing_buffer_during_initial_reset(self):
        scene = self.scene
        scene.capture = None
        scene.objects = []
        scene.fixture_nodes = []
        scene.fixture_names = []
        scene.initial_position = [0,0,0]
        scene.initial_rotation = [0,0,1,0]
        scene.reset()
        self.assertEqual(scene.drive.robot.step.call_count, 2)
        self.assertFalse(json.loads(Path(str(scene.preview_path)+'.json').read_text())['ready'])

    def test_missing_buffer_does_not_interrupt_move(self):
        self.assertFalse(self.scene.drive.move(0.5))  # stationary fixture reaches normal stall
        self.assertEqual(self.scene.drive.robot.step.call_count, 63)
        self.scene.drive.node.setVelocity.assert_called_with([0.0] * 6)

    def test_preserve_frame_and_recover_after_missing_buffer(self):
        scene = self.scene
        scene.camera_pixels.side_effect = None
        scene.camera_pixels.return_value = Image.new('RGB', (640,480), 'red')
        scene.publish_preview()
        frame = scene.preview_path.read_bytes()
        scene.camera_pixels.side_effect = ValueError('NULL pointer access')
        scene.publish_preview()
        self.assertEqual(scene.preview_path.read_bytes(), frame)
        self.assertFalse(scene.preview_ready)
        scene.camera_pixels.side_effect = None
        scene.publish_preview()
        self.assertTrue(scene.preview_ready)

    def test_idle_heartbeat_never_steps_physics_or_reads_camera(self):
        self.scene.preview_ready = True
        self.scene.preview_heartbeat()
        self.scene.drive.robot.step.assert_not_called()
        self.scene.camera_pixels.assert_not_called()
        self.assertTrue(json.loads(Path(str(self.scene.preview_path)+'.json').read_text())['ready'])
