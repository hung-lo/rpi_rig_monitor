import os
import unittest

from monitor.config import (
    DEFAULT_IMAGE_DIR,
    DEFAULT_NATURAL_IMAGE_DIR,
    DEFAULT_SIMULATION_SESSION_ROOT,
    parse_args,
)


class ConfigTests(unittest.TestCase):
    def test_default_image_directory(self):
        self.assertEqual(parse_args([]).image_dir, DEFAULT_IMAGE_DIR)
        self.assertEqual(parse_args([]).natural_image_dir, DEFAULT_NATURAL_IMAGE_DIR)
        self.assertEqual(parse_args([]).simulation_session_root, DEFAULT_SIMULATION_SESSION_ROOT)

    def test_cli_image_directory_override(self):
        self.assertEqual(parse_args(["--image-dir", "/tmp/images"]).image_dir, "/tmp/images")
        self.assertEqual(
            parse_args(["--natural-image-dir", "/tmp/natural"]).natural_image_dir,
            "/tmp/natural",
        )

    def test_supervisor_path_overrides(self):
        args = parse_args(["--supervisor-state-dir", "/tmp/rig-state",
                           "--supervisor-socket", "/tmp/rig.sock"])
        self.assertEqual(args.supervisor_state_dir, "/tmp/rig-state")
        self.assertEqual(args.supervisor_socket, "/tmp/rig.sock")

    def test_session_root_overrides(self):
        args = parse_args(["--session-root", "/tmp/sessions",
                           "--simulation-session-root", "/tmp/simulations"])
        self.assertEqual(args.session_root, "/tmp/sessions")
        self.assertEqual(args.simulation_session_root, "/tmp/simulations")

    def test_simulation_session_root_environment_override(self):
        previous = os.environ.get("RIG_MONITOR_SIMULATION_SESSION_ROOT")
        try:
            os.environ["RIG_MONITOR_SIMULATION_SESSION_ROOT"] = "/tmp/from-env"
            self.assertEqual(parse_args([]).simulation_session_root, "/tmp/from-env")
            self.assertEqual(parse_args(["--simulation-session-root", "/tmp/from-cli"])
                             .simulation_session_root, "/tmp/from-cli")
        finally:
            if previous is None:
                os.environ.pop("RIG_MONITOR_SIMULATION_SESSION_ROOT", None)
            else:
                os.environ["RIG_MONITOR_SIMULATION_SESSION_ROOT"] = previous

    def test_control_token_is_read_from_environment_not_process_arguments(self):
        previous = os.environ.get("RIG_MONITOR_CONTROL_TOKEN")
        try:
            os.environ["RIG_MONITOR_CONTROL_TOKEN"] = "secret"
            self.assertEqual(parse_args([]).control_token, "secret")
        finally:
            if previous is None:
                os.environ.pop("RIG_MONITOR_CONTROL_TOKEN", None)
            else:
                os.environ["RIG_MONITOR_CONTROL_TOKEN"] = previous

    def test_environment_image_directory_override(self):
        previous = os.environ.get("RIG_MONITOR_IMAGE_DIR")
        try:
            os.environ["RIG_MONITOR_IMAGE_DIR"] = "/tmp/from-env"
            self.assertEqual(parse_args([]).image_dir, "/tmp/from-env")
            self.assertEqual(parse_args(["--image-dir", "/tmp/from-cli"]).image_dir, "/tmp/from-cli")
        finally:
            if previous is None:
                os.environ.pop("RIG_MONITOR_IMAGE_DIR", None)
            else:
                os.environ["RIG_MONITOR_IMAGE_DIR"] = previous

    def test_environment_natural_image_directory_override(self):
        previous = os.environ.get("RIG_MONITOR_NATURAL_IMAGE_DIR")
        try:
            os.environ["RIG_MONITOR_NATURAL_IMAGE_DIR"] = "/tmp/natural-from-env"
            self.assertEqual(parse_args([]).natural_image_dir, "/tmp/natural-from-env")
            self.assertEqual(
                parse_args(["--natural-image-dir", "/tmp/natural-from-cli"]).natural_image_dir,
                "/tmp/natural-from-cli",
            )
        finally:
            if previous is None:
                os.environ.pop("RIG_MONITOR_NATURAL_IMAGE_DIR", None)
            else:
                os.environ["RIG_MONITOR_NATURAL_IMAGE_DIR"] = previous


if __name__ == "__main__":
    unittest.main()
