import io
import os
import unittest
from threading import Lock
from unittest.mock import Mock, patch

with patch.dict(os.environ, {"ROVER_LAB_RUN_DIR": "runs/gateway-test"}):
    from integration.docker import gateway


class GatewayOriginTests(unittest.TestCase):
    def post(self, origin, origins, path="/scenario-1/api/run/start"):
        handler = object.__new__(gateway.Gateway)
        handler.headers = {"Content-Length": "2"}
        if origin is not None:
            handler.headers["Origin"] = origin
        handler.path = path
        handler.rfile = io.BytesIO(b"{}")
        handler.connection = Mock()
        handler.error = Mock()
        handler.forward = Mock()
        scenario = 1 if path.startswith("/scenario-1/api/") else 2
        simulator = Mock(current=scenario, lock=Lock())
        simulator.healthy.return_value = True
        with patch.object(gateway, "ORIGINS", origins), patch.object(gateway, "simulator", simulator):
            handler.do_POST()
        return handler

    @patch.dict(os.environ, {}, clear=True)
    def test_default_local_addresses_can_start_and_reset(self):
        origins = gateway.allowed_origins(10000)
        for origin in ("http://localhost:10000", "http://127.0.0.1:10000"):
            for path in ("/scenario-1/api/run/start", "/scenario-1/api/run/reset", "/start"):
                handler = self.post(origin, origins, path)
                handler.forward.assert_called_once_with(b"{}")
                handler.error.assert_not_called()

    @patch.dict(os.environ, {"ROVER_PUBLIC_URL": "http://localhost:8113/"}, clear=True)
    def test_published_port_uses_public_url(self):
        origins = gateway.allowed_origins(10000)
        self.post("http://localhost:8113", origins).forward.assert_called_once()
        self.post("http://localhost:10000", origins).error.assert_called_once_with(403, "Origin rejected")

    @patch.dict(os.environ, {"RENDER_EXTERNAL_URL": "https://rover.example.com/"}, clear=True)
    def test_render_fallback_and_explicit_override(self):
        self.assertEqual(gateway.allowed_origins(10000), {"https://rover.example.com"})
        with patch.dict(os.environ, {"ROVER_PUBLIC_URL": "https://demo.example.com"}):
            self.assertEqual(gateway.allowed_origins(10000), {"https://demo.example.com"})

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_and_foreign_origins_never_forward(self):
        origins = gateway.allowed_origins(10000)
        for origin in (None, "null", "https://attacker.example", "http://localhost:10000.evil"):
            handler = self.post(origin, origins)
            handler.error.assert_called_once_with(403, "Origin rejected")
            handler.forward.assert_not_called()

    @patch.dict(os.environ, {}, clear=True)
    def test_invalid_public_urls_fail_at_startup(self):
        for url in ("file:///app", "https://user:secret@example.com", "https://example.com/path",
                    "https://example.com?query=1", "https://example.com#fragment"):
            with self.subTest(url=url), patch.dict(os.environ, {"ROVER_PUBLIC_URL": url}):
                with self.assertRaises(ValueError):
                    gateway.allowed_origins(10000)
