import json
import os
import tempfile
import unittest
from pathlib import Path

from monitor.profiles import ProfileError, ProfileStore
from monitor.registry import Preset, Protocol, ProtocolRegistry, SessionParameter


class ProfileStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "profiles"
        parameter = SessionParameter("image_count", "Image count", "integer", 5,
                                     "Number of selected images.", "--image-count", 1, 2800, "images")
        protocol = Protocol("natural_images", "Natural Images", "", "unix_json_v1",
                            ("/bin/true",), (Preset("operator_simulation", "Simulation", "", (),
                                                    parameter_defaults={"image_count": 5}),),
                            parameters=(parameter,))
        self.registry = ProtocolRegistry((protocol,))
        self.store = ProfileStore(self.root, self.registry)

    def tearDown(self):
        self.temporary.cleanup()

    def test_save_load_update_delete_and_restart_persistence(self):
        profile = self.store.save_profile("natural_images", "operator_simulation", "Short run",
                                          {"image_count": 12})
        path = (self.root / "natural_images" / "operator_simulation" /
                (profile["profile_id"] + ".json"))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(profile["version"], 1)
        self.assertEqual(self.store.list_profiles("natural_images", "operator_simulation")[0]["parameters"],
                         {"image_count": 12})
        restarted = ProfileStore(self.root, self.registry)
        loaded = restarted.get_profile("natural_images", "operator_simulation", profile["profile_id"], 1)
        self.assertEqual(loaded["parameters"], {"image_count": 12})
        updated = restarted.update_profile("natural_images", "operator_simulation", profile["profile_id"],
                                           1, "Long run", {"image_count": 24})
        self.assertEqual(updated["version"], 2)
        with self.assertRaisesRegex(ProfileError, "changed by another operator"):
            restarted.update_profile("natural_images", "operator_simulation", profile["profile_id"],
                                     1, "Stale edit", {"image_count": 30})
        self.assertEqual(restarted.delete_profile("natural_images", "operator_simulation",
                                                  profile["profile_id"], 2),
                         {"deleted": True, "profile_id": profile["profile_id"]})
        self.assertEqual(restarted.list_profiles("natural_images", "operator_simulation"), [])

    def test_duplicate_names_and_invalid_values_are_rejected(self):
        self.store.save_profile("natural_images", "operator_simulation", "Baseline", {"image_count": 5})
        with self.assertRaisesRegex(ProfileError, "already exists"):
            self.store.save_profile("natural_images", "operator_simulation", "baseline", {"image_count": 6})
        for values in ({"image_count": "10"}, {"image_count": True},
                       {"image_count": 2, "rule_switch_block": 10}, {"image_count": 2801}):
            with self.subTest(values=values), self.assertRaises(ProfileError):
                self.store.save_profile("natural_images", "operator_simulation", "Bad", values)
        for name in ("../escape", "", "bad\nname", "x" * 65):
            with self.subTest(name=name), self.assertRaises(ProfileError):
                self.store.save_profile("natural_images", "operator_simulation", name, {"image_count": 5})

    def test_corrupt_schema_mismatch_and_deprecated_parameter_are_visible_as_invalid(self):
        profile = self.store.save_profile("natural_images", "operator_simulation", "Editable",
                                          {"image_count": 5})
        path = (self.root / "natural_images" / "operator_simulation" /
                (profile["profile_id"] + ".json"))
        document = json.loads(path.read_text())
        document["parameters"] = {"image_count": 12, "rule_switch_block": 4}
        path.write_text(json.dumps(document))
        os.chmod(str(path), 0o600)
        listed = self.store.list_profiles("natural_images", "operator_simulation")[0]
        self.assertFalse(listed["valid"])
        self.assertEqual(listed["parameters"], {"image_count": 12})
        # An explicit update can correct a deprecated field; it is never defaulted silently.
        corrected = self.store.update_profile("natural_images", "operator_simulation", profile["profile_id"],
                                              1, "Editable", {"image_count": 14})
        self.assertEqual(corrected["parameters"], {"image_count": 14})
        document = json.loads(path.read_text())
        document["schema_version"] = 99
        path.write_text(json.dumps(document))
        os.chmod(str(path), 0o600)
        self.assertFalse(self.store.list_profiles("natural_images", "operator_simulation")[0]["valid"])
        with self.assertRaisesRegex(ProfileError, "schema or identity"):
            self.store.get_profile("natural_images", "operator_simulation", profile["profile_id"])

    def test_corrupted_json_can_be_removed_after_explicit_operator_confirmation(self):
        profile = self.store.save_profile("natural_images", "operator_simulation", "Broken",
                                          {"image_count": 5})
        path = (self.root / "natural_images" / "operator_simulation" /
                (profile["profile_id"] + ".json"))
        path.write_text("{ truncated")
        os.chmod(str(path), 0o600)
        listed = self.store.list_profiles("natural_images", "operator_simulation")[0]
        self.assertFalse(listed["valid"])
        self.assertIsNone(listed["version"])
        self.assertEqual(self.store.delete_profile("natural_images", "operator_simulation",
                                                   profile["profile_id"], None),
                         {"deleted": True, "profile_id": profile["profile_id"]})


if __name__ == "__main__":
    unittest.main()
