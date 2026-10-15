"""统计默认关闭；所有配置和网络均隔离，不读取开发者的 .env。"""
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


BACKEND = Path(__file__).resolve().parents[1]


def isolated_config(environment=None):
    spec = importlib.util.spec_from_file_location("telemetry_test_config", BACKEND / "config.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, environment or {}, clear=True), \
            patch.object(Path, "exists", return_value=False), \
            patch.object(Path, "read_text", side_effect=AssertionError("测试不得读取 .env")):
        spec.loader.exec_module(module)
    return module


def isolated_telemetry(config):
    spec = importlib.util.spec_from_file_location("telemetry_test_module", BACKEND / "telemetry.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"config": config}):
        spec.loader.exec_module(module)
    return module


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.config = isolated_config()
        self.telemetry = isolated_telemetry(self.config)
        self.client_factory = self.enterContext(patch.object(self.telemetry.httpx, "Client"))
        self.client = self.client_factory.return_value.__enter__.return_value
        self.client.post.return_value = MagicMock()

    def opt_in(self):
        self.config.TEAMFLOW_USAGE_URL = "https://usage.example.invalid/events"

    def test_defaults_are_local_and_do_not_transmit(self):
        self.assertEqual(self.config.PUBLIC_BASE, f"http://127.0.0.1:{self.config.PORT}")
        self.assertEqual(self.config.TEAMFLOW_USAGE_URL, "")
        self.telemetry.report_usage("session-1", "测试使用人")
        self.client_factory.assert_not_called()

    def test_local_public_base_tracks_configured_port_without_reading_dotenv(self):
        config = isolated_config({"FANGTAN_PORT": "18995"})
        self.assertEqual(config.PUBLIC_BASE, "http://127.0.0.1:18995")

    def test_explicit_public_base_and_usage_url_are_preserved(self):
        config = isolated_config({
            "FANGTAN_PUBLIC_BASE": "https://interview.example.invalid",
            "TEAMFLOW_USAGE_URL": "https://usage.example.invalid/events",
        })
        self.assertEqual(config.PUBLIC_BASE, "https://interview.example.invalid")
        self.assertEqual(config.TEAMFLOW_USAGE_URL, "https://usage.example.invalid/events")

    def test_auth_disabled_never_creates_client_even_with_opt_in(self):
        self.opt_in()
        self.config.AUTH_ENABLED = False
        self.telemetry.report_usage("session-1", "测试使用人")
        self.client_factory.assert_not_called()

    def test_blank_url_never_creates_client(self):
        self.config.TEAMFLOW_USAGE_URL = " \t\n"
        self.telemetry.report_usage("session-1", "测试使用人")
        self.client_factory.assert_not_called()

    def test_opt_in_preserves_stable_event_and_product_id_payload(self):
        self.opt_in()
        self.config.TEAMFLOW_PRODUCT_ID = "42"
        expected = {
            "user_name": "测试使用人",
            "event_id": "fangtan-report-session-1",
            "source": "fangtan-agentic",
            "remark": "访谈纪要生成成功：session-1",
            "usage_count": 1,
            "product_id": 42,
        }
        self.telemetry.report_usage("session-1", "  测试使用人  ")
        self.telemetry.report_usage("session-1", "  测试使用人  ")
        self.assertEqual(self.client.post.call_count, 2)
        for call in self.client.post.call_args_list:
            self.assertEqual(call.args, (self.config.TEAMFLOW_USAGE_URL,))
            self.assertEqual(call.kwargs, {"json": expected})
        self.client_factory.assert_called_with(timeout=10)
        self.assertEqual(self.client.post.return_value.raise_for_status.call_count, 2)

    def test_opt_in_without_product_id_uses_name_and_trims_url(self):
        self.opt_in()
        self.config.TEAMFLOW_USAGE_URL = "  " + self.config.TEAMFLOW_USAGE_URL + "  "
        self.config.TEAMFLOW_PRODUCT_NAME = "测试产品"
        self.telemetry.report_usage("session-2", "测试使用人")
        self.assertEqual(self.client.post.call_args.args, ("https://usage.example.invalid/events",))
        payload = self.client.post.call_args.kwargs["json"]
        self.assertEqual(payload["product_name"], "测试产品")
        self.assertNotIn("product_id", payload)

    def test_invalid_product_id_does_not_request(self):
        self.opt_in()
        self.config.TEAMFLOW_PRODUCT_ID = "not-an-id"
        with self.assertLogs("fangtan.telemetry", level="ERROR"):
            self.telemetry.report_usage("session-1", "测试使用人")
        self.client_factory.assert_not_called()

    def test_missing_user_does_not_request(self):
        self.opt_in()
        with self.assertLogs("fangtan.telemetry", level="WARNING"):
            self.telemetry.report_usage("session-1", " \t")
        self.client_factory.assert_not_called()

    def test_network_failure_does_not_block_report(self):
        self.opt_in()
        self.client.post.side_effect = self.telemetry.httpx.ConnectError("offline fixture")
        with self.assertLogs("fangtan.telemetry", level="ERROR"):
            self.assertIsNone(self.telemetry.report_usage("session-1", "测试使用人"))

    def test_http_error_does_not_block_report(self):
        self.opt_in()
        self.client.post.return_value.raise_for_status.side_effect = RuntimeError("HTTP fixture")
        with self.assertLogs("fangtan.telemetry", level="ERROR"):
            self.assertIsNone(self.telemetry.report_usage("session-1", "测试使用人"))


if __name__ == "__main__":
    unittest.main()
