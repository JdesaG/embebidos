from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from csi_web import server


class ManualClock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


class ManualHoldCollectionTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.clock = ManualClock(100.0)
        self.clock_patch = patch.object(server.time, "monotonic", self.clock)
        self.clock_patch.start()
        self.recorder = server.CsiRecorder(self.base_dir)

    def tearDown(self):
        if self.recorder.active:
            self.recorder.stop("test_cleanup")
        self.clock_patch.stop()
        self.temp_dir.cleanup()

    def start_manual_session(self):
        return self.recorder.start(
            {
                "label": "hold_breath_off",
                "duration_s": 180,
                "session_id": "test_manual_hold",
            }
        )

    def test_manual_hold_starts_at_sixty_seconds_and_ends_at_button_time(self):
        initial = self.start_manual_session()
        self.assertTrue(initial["manual_hold"])
        self.assertFalse(initial["can_end_hold"])
        self.assertEqual(initial["event_label"], "breathing")

        self.clock.now = 159.9
        before_alarm = self.recorder.snapshot()
        self.assertFalse(before_alarm["hold_started"])
        self.assertAlmostEqual(before_alarm["schedule"]["phase_remaining_s"], 0.1, places=3)

        self.clock.now = 160.0
        during_hold = self.recorder.snapshot()
        self.assertTrue(during_hold["hold_started"])
        self.assertTrue(during_hold["can_end_hold"])
        self.assertEqual(during_hold["event_label"], "hold_breath")
        self.assertIsNone(during_hold["schedule"]["phase_remaining_s"])

        self.clock.now = 172.5
        completed = self.recorder.end_manual_hold()
        self.assertEqual(completed["stop_reason"], "participant_ended_hold")
        self.assertEqual(completed["manual_hold_ended_s"], 72.5)
        self.assertEqual(completed["hold_duration_s"], 12.5)
        self.assertTrue(completed["valid_for_training"])
        self.assertEqual(completed["events"], [
            {"start_s": 0.0, "end_s": 60.0, "label": "breathing"},
            {"start_s": 60.0, "end_s": 72.5, "label": "hold_breath"},
        ])

        metadata_path = self.base_dir / completed["metadata_file"]
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["hold_duration_s"], 12.5)
        self.assertEqual(metadata["stop_reason"], "participant_ended_hold")

    def test_button_is_rejected_before_alarm(self):
        self.start_manual_session()
        self.clock.now = 140.0
        with self.assertRaisesRegex(RuntimeError, "alarma"):
            self.recorder.end_manual_hold()
        self.assertTrue(self.recorder.active)

    def test_regular_collection_remains_timed(self):
        snapshot = self.recorder.start(
            {
                "label": "breathing_off",
                "duration_s": 90,
                "session_id": "test_regular",
            }
        )
        self.assertFalse(snapshot["manual_hold"])
        self.assertFalse(snapshot["can_end_hold"])
        self.assertEqual(snapshot["event_label"], "breathing_off")

    def test_cancelled_hold_is_not_marked_as_completed_hold(self):
        self.start_manual_session()
        self.clock.now = 168.0
        completed = self.recorder.stop("manual")
        self.assertEqual(completed["stop_reason"], "manual")
        self.assertIsNone(completed["manual_hold_ended_s"])
        self.assertIsNone(completed["hold_duration_s"])
        self.assertFalse(completed["valid_for_training"])


if __name__ == "__main__":
    unittest.main()
