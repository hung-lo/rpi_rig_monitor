import os
import unittest

from monitor.config import DEFAULT_IMAGE_DIR, parse_args


class ConfigTests(unittest.TestCase):
    def test_default_image_directory(self):
        self.assertEqual(parse_args([]).image_dir, DEFAULT_IMAGE_DIR)

    def test_cli_image_directory_override(self):
        self.assertEqual(parse_args(["--image-dir", "/tmp/images"]).image_dir, "/tmp/images")

    def test_supervisor_path_overrides(self):
        args = parse_args(["--supervisor-state-dir", "/tmp/rig-state",
                           "--supervisor-socket", "/tmp/rig.sock"])
        self.assertEqual(args.supervisor_state_dir, "/tmp/rig-state")
        self.assertEqual(args.supervisor_socket, "/tmp/rig.sock")

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


if __name__ == "__main__":
    unittest.main()
