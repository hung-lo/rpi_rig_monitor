import unittest
import os
import tempfile
from pathlib import Path

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
                           "water-delivered", "water-likely-consumed", "reward-volume",
                           "stimulus-preview-empty"):
            self.assertIn(('id="%s"' % element_id).encode("utf-8"), dashboard.data)
        for element_id in ("spout-reward-progress", "spout-retrieval-rate", "spout-recent20",
                           "spout-criterion", "spout-training-water", "spout-bait-water",
                           "spout-total-water", "spout-total-licks", "spout-bait-drops",
                           "spout-bait-contact", "spout-recent-licks", "spout-last-lick",
                           "spout-lick-status", "last-panel-title", "history-title"):
            self.assertIn(('id="%s"' % element_id).encode("utf-8"), dashboard.data)
        for element_id in ("contingency-phase", "phase-contingency", "partial-reversal-behavior-view",
                           "new-reward-contact-rate", "r-plus-anticipatory-rate", "r-minus-anticipatory-rate", "r-discrimination",
                           "r-plus-omission-rate", "reversal-readiness", "high-r-plus-rate", "high-r-minus-rate",
                           "medium-r-plus-rate", "medium-r-minus-rate", "low-r-minus-rate",
                           "partial-reversal-history-head"):
            self.assertIn(('id="%s"' % element_id).encode("utf-8"), dashboard.data)

        javascript = Path(__file__).resolve().parents[1].joinpath("static", "dashboard.js").read_text()
        css = Path(__file__).resolve().parents[1].joinpath("static", "dashboard.css").read_text()
        self.assertIn(".preview img[hidden] { display: none; }", css)
        self.assertIn('empty.hidden = false', javascript)
        self.assertIn('message.hidden = false', javascript)
        self.assertIn('empty.hidden = true', javascript)
        self.assertIn("var hasSession =", javascript)
        self.assertIn('data.protocol === "spout_training"', javascript)
        self.assertIn('data.protocol_version === "exposure_reward_partial_reversal_v1"', javascript)
        self.assertIn("renderPartialReversalBehavior", javascript)
        self.assertIn("task_r_plus_cue_anticipatory_lick_trials_session", javascript)
        self.assertIn("task_r_minus_cue_anticipatory_lick_trials_session", javascript)
        self.assertIn("reversal-readiness", javascript)
        self.assertIn('var isBaitPhase = data.phase === "MANUAL_BAIT" || data.phase === "MANUAL_START_DELAY";', javascript)
        self.assertIn('var isBaitView = isSpout && (isBaitPhase || (!data.phase && data.manual_bait_active === true));', javascript)
        self.assertNotIn('data.manual_bait_active === true);', javascript)
        self.assertIn('setHidden("spout-behavior-scheduled", isBaitView)', javascript)
        self.assertIn('setHidden("spout-behavior-manual", !isBaitView)', javascript)
        self.assertIn('data.phase === "MANUAL_START_DELAY"', javascript)
        self.assertIn('window.setInterval(poll, 300)', javascript)
        self.assertIn("pollInFlight", javascript)
        self.assertIn(b"No active stimulus", dashboard.data)
        self.assertIn(b"Stimulus image unavailable", dashboard.data)
        state_response = client.get("/api/state")
        self.assertEqual(state_response.status_code, 200)
        self.assertIn("connected", state_response.get_json())
        self.assertEqual(client.get("/health").get_json(), {"status": "ok", "udp_receiver": "ok"})

    def test_reward_volume_display_has_no_train_suffix(self):
        javascript = Path(__file__).resolve().parents[1].joinpath("static", "dashboard.js").read_text()
        self.assertIn('$("reward-volume").textContent = formatWater(data.reward_volume_ul_per_train, false);', javascript)
        self.assertNotIn('formatWater(data.reward_volume_ul_per_train, false) + "/train"', javascript)

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
