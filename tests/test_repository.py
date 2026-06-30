from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timezone

from group_bot.db import migrate
from group_bot.repository import Repository, UserProfile


def make_repo() -> Repository:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn)
    return Repository(conn)


class RepositoryTest(unittest.TestCase):
    def test_attendance_is_once_per_day(self) -> None:
        repo = make_repo()
        now = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)

        first = repo.record_attendance(1, 10, "sign", "2026-07-01", now)
        second = repo.record_attendance(1, 10, "sign", "2026-07-01", now)

        self.assertTrue(first)
        self.assertFalse(second)
        rows = repo.get_attendance(1, 10, "2026-07-01")
        self.assertEqual(len(rows), 1)

    def test_task_progress_and_done(self) -> None:
        repo = make_repo()
        now = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
        repo.upsert_user(UserProfile(1, 10, "alice", "Alice"), now)

        task_id = repo.create_task(1, 10, "alice", "整理日报", "2026-07-01", 99, now)
        repo.add_progress(1, task_id, 10, "完成一半", now)
        repo.mark_done(1, task_id, 10, "已完成", now)

        task = repo.get_task(1, task_id)
        progress = repo.progress_for_tasks([task_id])

        self.assertIsNotNone(task)
        self.assertEqual(task["status"], "done")
        self.assertEqual(len(progress[task_id]), 2)


if __name__ == "__main__":
    unittest.main()
