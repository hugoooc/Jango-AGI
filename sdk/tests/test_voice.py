import unittest

from sdk_product.voice import _turn_has_ended, build_parser, normalize_request


class VoiceTests(unittest.TestCase):
    def test_normalizes_transcript(self):
        self.assertEqual(normalize_request("  mass   and\nCG  "), "mass and CG")

    def test_default_capture_cannot_be_cut_off_by_vad(self):
        args = build_parser().parse_args([])
        self.assertEqual(args.max_seconds, 10.0)
        self.assertGreater(args.vad_threshold, 1.0)

    def test_vad_can_be_enabled_explicitly(self):
        message = {
            "type": "step",
            "vad": [{"horizon_s": 2.0, "inactivity_prob": 0.9}],
        }
        self.assertTrue(_turn_has_ended(message, heard_text=True, threshold=0.8))


if __name__ == "__main__":
    unittest.main()
