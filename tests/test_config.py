import os
import unittest

from monitor.config import DEFAULT_IMAGE_DIR, parse_args


class ConfigTests(unittest.TestCase):
    def test_default_image_directory(self):
        self.assertEqual(parse_args([]).image_dir, DEFAULT_IMAGE_DIR)

    def test_cli_image_directory_override(self):
        self.assertEqual(parse_args(["--image-dir", "/tmp/images"]).image_dir, "/tmp/images")

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
