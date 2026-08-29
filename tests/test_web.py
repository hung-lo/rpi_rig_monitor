import unittest
import os
import tempfile

from monitor.state import MonitorState
from monitor.web import create_app


class HealthyReceiver(object):
    def health_status(self):
        return "ok"


class FailedReceiver(object):
    def health_status(self):
        return "failed"


class WebTests(unittest.TestCase):
    def test_dashboard_and_json_routes(self):
        client = create_app(MonitorState(), HealthyReceiver()).test_client()
        dashboard = client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        for element_id in ("reward-contact-rate", "rewarded-cue-anticipatory-rate",
                           "unrewarded-cue-anticipatory-rate", "low-prob-cue-anticipatory-rate",
                           "water-delivered", "water-likely-consumed", "reward-volume"):
            self.assertIn(('id="%s"' % element_id).encode("utf-8"), dashboard.data)
        state_response = client.get("/api/state")
        self.assertEqual(state_response.status_code, 200)
        self.assertIn("connected", state_response.get_json())
        self.assertEqual(client.get("/health").get_json(), {"status": "ok", "udp_receiver": "ok"})

    def test_unhealthy_receiver_is_not_reported_as_healthy(self):
        app = create_app(MonitorState(), FailedReceiver())
        response = app.test_client().get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["status"], "unhealthy")

    def test_existing_stimulus_image_is_served(self):
        with tempfile.TemporaryDirectory() as image_dir:
            image_path = os.path.join(image_dir, "test.png")
            with open(image_path, "wb") as image_file:
                image_file.write(b"test image bytes")
            app = create_app(MonitorState(), HealthyReceiver(), image_dir=image_dir)
            response = app.test_client().get("/stimulus-image/test.png")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, b"test image bytes")
            response.close()

    def test_missing_stimulus_image_is_404(self):
        with tempfile.TemporaryDirectory() as image_dir:
            response = create_app(MonitorState(), HealthyReceiver(), image_dir=image_dir).test_client().get("/stimulus-image/missing.png")
            self.assertEqual(response.status_code, 404)

    def test_missing_image_directory_does_not_break_monitor(self):
        app = create_app(MonitorState(), HealthyReceiver(), image_dir="/path/that/does/not/exist")
        client = app.test_client()
        self.assertEqual(client.get("/").status_code, 200)
        self.assertEqual(client.get("/api/state").status_code, 200)
        self.assertEqual(client.get("/stimulus-image/test.png").status_code, 404)

    def test_stimulus_image_cannot_escape_directory(self):
        with tempfile.TemporaryDirectory() as image_dir, tempfile.TemporaryDirectory() as outside_dir:
            with open(os.path.join(outside_dir, "secret.txt"), "wb") as secret:
                secret.write(b"not for the dashboard")
            response = create_app(MonitorState(), HealthyReceiver(), image_dir=image_dir).test_client().get("/stimulus-image/../secret.txt")
            self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
