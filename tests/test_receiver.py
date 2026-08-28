import json
import unittest

from monitor.receiver import decode_packet


class ReceiverTests(unittest.TestCase):
    def test_malformed_packet_returns_none(self):
        self.assertIsNone(decode_packet(b"not json"))
        self.assertIsNone(decode_packet(b"{\xff"))

    def test_unknown_fields_are_safe(self):
        packet = {"type": "state", "schema_version": 1, "new_protocol_field": {"value": 3}}
        decoded = decode_packet(json.dumps(packet).encode("utf-8"))
        self.assertEqual(decoded["new_protocol_field"], {"value": 3})

    def test_non_object_and_missing_type_are_rejected(self):
        self.assertIsNone(decode_packet(b"[]"))
        self.assertIsNone(decode_packet(b"{}"))


if __name__ == "__main__":
    unittest.main()
