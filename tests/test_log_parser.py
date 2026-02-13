from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.attempt_tracker import AttemptTracker
from app.database import Database
from app.log_parser import parse_log_line
from app.metrics import compute_summary


class TestLogParser(unittest.TestCase):
    def test_parse_chat_line(self) -> None:
        raw = "[21:07:24] [Render thread/INFO]: [CHAT]   Time: 28.95s"
        parsed = parse_log_line(raw)
        self.assertTrue(parsed.is_chat)
        self.assertEqual(parsed.clock_time, "21:07:24")
        self.assertEqual(parsed.chat_message, "Time: 28.95s")
        self.assertEqual(parsed.source, "CHAT")

    def test_parse_non_chat_line(self) -> None:
        raw = (
            "[21:09:37] [Server thread/INFO]: Saving chunks for level "
            "'ServerLevel[Zero Practice]'"
        )
        parsed = parse_log_line(raw)
        self.assertFalse(parsed.is_chat)
        self.assertIsNone(parsed.chat_message)


class TestAttemptTracker(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tempdir.name) / "test.db"
        self.db = Database(db_path)
        self.tracker = AttemptTracker(self.db)
        self.event_counter = 0

    def tearDown(self) -> None:
        self.db.close()
        self.tempdir.cleanup()

    def _push_chat(self, message: str, clock: str = "21:00:00") -> None:
        self.event_counter += 1
        event_id = self.db.execute(
            """
            INSERT INTO raw_log_events (
                ingested_at_utc, clock_time, thread_name, level, source,
                is_chat, chat_message, raw_line, file_offset
            )
            VALUES (
                '2026-02-11T00:00:00+00:00', ?, 'Render thread', 'INFO', 'CHAT',
                1, ?, ?, 0
            )
            """,
            (clock, message, message),
        )
        self.tracker.handle_chat_event(event_id=event_id, chat_message=message, clock_time=clock)

    def _push_zdash_context(
        self,
        tower: str = "Tall Boy (103)",
        zero_type: str = "Front Diagonal CW",
        clock: str = "21:00:00",
    ) -> None:
        self._push_chat(f"[ZDASH] Tower: {tower}", clock)
        self._push_chat(f"[ZDASH] Type: {zero_type}", clock)

    def test_successful_attempt(self) -> None:
        self._push_zdash_context("Tall Boy (103)", "Front Diagonal CWW")
        self._push_chat("17.60s 1st Bed Placed")
        self._push_chat("Damage: 50")
        self._push_chat("Damage: 49")
        self._push_chat("Dragon Killed!")
        self._push_chat("Explosives: 3+2")
        self._push_chat("Time: 29.60s")
        self._push_chat("Tower: Tall Boy (103)")
        self._push_chat("Type: Front Diagonal CWW")
        self._push_chat("Standing Height: 95")

        row = self.db.query_one("SELECT * FROM attempts ORDER BY id DESC LIMIT 1")
        assert row is not None
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["tower_name"], "Tall Boy")
        self.assertEqual(row["tower_code"], "103")
        self.assertEqual(row["zero_type"], "Front Diagonal CWW")
        self.assertEqual(row["bed_count"], 2)
        self.assertEqual(row["total_damage"], 99)
        self.assertEqual(row["standing_height"], 95)
        self.assertEqual(row["explosives_used"], 3)
        self.assertEqual(row["explosives_left"], 2)

    def test_fail_on_new_attempt(self) -> None:
        self._push_zdash_context("Tall Boy (103)", "Front Diagonal CW", "21:01:00")
        self._push_chat("18.00s 1st Bed Placed", "21:01:00")
        self._push_chat("Damage: 35", "21:01:02")
        self._push_zdash_context("Small Cage (79)", "Back Diagonal CW", "21:01:20")
        self._push_chat("17.40s 1st Bed Placed", "21:01:20")

        rows = self.db.query_all("SELECT id, status, fail_reason FROM attempts ORDER BY id ASC")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["status"], "fail")
        self.assertEqual(rows[0]["fail_reason"], "new_attempt_started")
        self.assertEqual(rows[1]["status"], "in_progress")

    def test_explosive_semantics_metrics(self) -> None:
        self._push_zdash_context("Tall Boy (103)", "Front Diagonal CW", "21:10:00")
        self._push_chat("16.00s 1st Bed Placed", "21:10:00")
        self._push_chat("Dragon Killed!", "21:10:10")
        self._push_chat("Explosives: 4+2", "21:10:10")

        self._push_zdash_context("Tall Boy (103)", "Front Diagonal CW", "21:11:00")
        self._push_chat("17.00s 1st Bed Placed", "21:11:00")
        self._push_chat("Dragon Killed!", "21:11:12")
        self._push_chat("Explosives: 2+2", "21:11:12")

        summary = compute_summary(self.db)
        self.assertEqual(summary["successes"], 2)
        self.assertEqual(summary["perfect_2_2_count"], 1)
        self.assertAlmostEqual(summary["avg_rotations_success"], 3.0, places=2)
        self.assertAlmostEqual(summary["avg_total_explosives_success"], 5.0, places=2)

    def test_explosives_single_value_parses_used_only(self) -> None:
        self._push_zdash_context("Tall Boy (103)", "Front Diagonal CW", "21:12:00")
        self._push_chat("16.80s 1st Bed Placed", "21:12:00")
        self._push_chat("Dragon Killed!", "21:12:10")
        self._push_chat("Explosives: 7", "21:12:10")
        self._push_chat("Time: 37.85s", "21:12:10")

        row = self.db.query_one("SELECT * FROM attempts ORDER BY id DESC LIMIT 1")
        assert row is not None
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["explosives_used"], 7)
        self.assertIsNone(row["explosives_left"])

        summary = compute_summary(self.db)
        self.assertEqual(summary["successes"], 1)
        self.assertAlmostEqual(summary["avg_rotations_success"], 7.0, places=2)
        self.assertAlmostEqual(summary["avg_total_explosives_success"], 7.0, places=2)

    def test_damage_kind_split_metrics(self) -> None:
        self._push_zdash_context("Tall Boy (103)", "Front Diagonal CW", "21:20:00")
        self._push_chat("16.00s 1st Bed Placed", "21:20:00")
        self._push_chat("Damage: 3", "21:20:01")
        self._push_chat("Damage: 42", "21:20:03")
        self._push_chat("Damage: 5", "21:20:05")
        self._push_chat("Damage: 39", "21:20:07")
        self._push_chat("Dragon Killed!", "21:20:09")

        attempt = self.db.query_one("SELECT * FROM attempts ORDER BY id DESC LIMIT 1")
        assert attempt is not None
        self.assertEqual(attempt["major_hit_count"], 2)
        self.assertEqual(attempt["setup_hit_count"], 2)
        self.assertEqual(attempt["major_damage_total"], 81)
        self.assertEqual(attempt["setup_damage_total"], 8)

        summary = compute_summary(self.db)
        self.assertAlmostEqual(summary["avg_damage_per_bed"], 40.5, places=2)
        self.assertNotIn("avg_setup_damage", summary)
        self.assertNotIn("setup_reach_rate", summary)

    def test_prefill_tower_type_from_zdash_start_logs(self) -> None:
        self._push_chat("[ZDASH] Tower: Tall Boy (103)", "21:30:00")
        self._push_chat("[ZDASH] Type: Front Diagonal CW", "21:30:00")
        self._push_chat("16.50s 1st Bed Placed", "21:30:02")

        row = self.db.query_one("SELECT tower_name, tower_code, zero_type FROM attempts ORDER BY id DESC LIMIT 1")
        assert row is not None
        self.assertEqual(row["tower_name"], "Tall Boy")
        self.assertEqual(row["tower_code"], "103")
        self.assertEqual(row["zero_type"], "Front Diagonal CW")

    def test_new_zdash_context_finalizes_open_attempt(self) -> None:
        self._push_chat("[ZDASH] Tower: Tall Boy (103)", "21:40:00")
        self._push_chat("[ZDASH] Type: Front Diagonal CCW", "21:40:00")
        self._push_chat("17.25s 1st Bed Placed", "21:40:03")
        self._push_chat("Damage: 42", "21:40:06")

        # New run context appears before next first-bed line.
        self._push_chat("[ZDASH] Tower: Small Cage (79)", "21:40:10")
        self._push_chat("[ZDASH] Type: Back Diagonal CW", "21:40:10")
        self._push_chat("18.10s 1st Bed Placed", "21:40:13")

        rows = self.db.query_all(
            "SELECT id,status,fail_reason,tower_name,zero_type FROM attempts ORDER BY id ASC"
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["status"], "fail")
        self.assertEqual(rows[0]["fail_reason"], "new_attempt_started")
        self.assertEqual(rows[0]["tower_name"], "Tall Boy")
        self.assertEqual(rows[0]["zero_type"], "Front Diagonal CCW")
        self.assertEqual(rows[1]["status"], "in_progress")
        self.assertEqual(rows[1]["tower_name"], "Small Cage")
        self.assertEqual(rows[1]["zero_type"], "Back Diagonal CW")

    def test_summary_excludes_setup_rate_metrics(self) -> None:
        self._push_zdash_context("Tall Boy (103)", "Front Diagonal CW", "21:50:00")
        self._push_chat("17.00s 1st Bed Placed", "21:50:00")
        self._push_chat("Dragon Killed!", "21:50:05")

        self._push_zdash_context("Tall Boy (103)", "Front Diagonal CW", "21:51:00")
        self._push_chat("17.20s 1st Bed Placed", "21:51:00")
        self._push_chat("Damage: 40", "21:51:02")
        self._push_chat("Dragon Killed!", "21:51:06")

        summary = compute_summary(self.db)
        self.assertEqual(summary["successes"], 2)
        self.assertEqual(summary["finished_attempts"], 2)
        self.assertNotIn("setup_reached_attempts", summary)
        self.assertNotIn("setup_reached_successes", summary)
        self.assertNotIn("setup_reach_rate", summary)
        self.assertNotIn("success_rate_after_setup", summary)


if __name__ == "__main__":
    unittest.main()
