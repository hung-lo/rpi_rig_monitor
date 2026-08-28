import unittest

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
        self.assertEqual(client.get("/").status_code, 200)
        state_response = client.get("/api/state")
        self.assertEqual(state_response.status_code, 200)
        self.assertIn("connected", state_response.get_json())
        self.assertEqual(client.get("/health").get_json(), {"status": "ok", "udp_receiver": "ok"})

    def test_unhealthy_receiver_is_not_reported_as_healthy(self):
        app = create_app(MonitorState(), FailedReceiver())
        response = app.test_client().get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["status"], "unhealthy")


if __name__ == "__main__":
    unittest.main()
