from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


@dataclass(frozen=True)
class UserProfile:
    chat_id: int
    user_id: int
    username: str | None
    full_name: str


class Repository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert_user(self, user: UserProfile, now: datetime) -> None:
        self.conn.execute(
            """
            INSERT INTO users (chat_id, user_id, username, full_name, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name,
                updated_at = excluded.updated_at
            """,
            (
                user.chat_id,
                user.user_id,
                user.username.lower() if user.username else None,
                user.full_name,
                now.isoformat(timespec="seconds"),
            ),
        )
        self.conn.commit()

    def find_user_by_username(self, chat_id: int, username: str) -> sqlite3.Row | None:
        normalized = username.removeprefix("@").lower()
        return self.conn.execute(
            """
            SELECT chat_id, user_id, username, full_name
            FROM users
            WHERE chat_id = ? AND username = ?
            """,
            (chat_id, normalized),
        ).fetchone()

    def record_attendance(
        self, chat_id: int, user_id: int, kind: str, record_date: str, now: datetime
    ) -> bool:
        cursor = self.conn.execute(
            """
            INSERT OR IGNORE INTO attendance_records
                (chat_id, user_id, kind, record_date, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (chat_id, user_id, kind, record_date, now.isoformat(timespec="seconds")),
        )
        self.conn.commit()
        return cursor.rowcount == 1

    def get_attendance(self, chat_id: int, user_id: int, record_date: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT kind, created_at
                FROM attendance_records
                WHERE chat_id = ? AND user_id = ? AND record_date = ?
                ORDER BY created_at
                """,
                (chat_id, user_id, record_date),
            )
        )

    def create_task(
        self,
        chat_id: int,
        assignee_user_id: int,
        assignee_username: str | None,
        title: str,
        task_date: str,
        created_by_user_id: int,
        now: datetime,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO tasks (
                chat_id, assignee_user_id, assignee_username, title,
                task_date, created_by_user_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                assignee_user_id,
                assignee_username.lower() if assignee_username else None,
                title,
                task_date,
                created_by_user_id,
                now.isoformat(timespec="seconds"),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def get_task(self, chat_id: int, task_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT *
            FROM tasks
            WHERE chat_id = ? AND id = ?
            """,
            (chat_id, task_id),
        ).fetchone()

    def list_tasks(self, chat_id: int, task_date: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT t.*, u.full_name
                FROM tasks t
                LEFT JOIN users u
                    ON u.chat_id = t.chat_id AND u.user_id = t.assignee_user_id
                WHERE t.chat_id = ? AND t.task_date = ?
                ORDER BY t.status, t.id
                """,
                (chat_id, task_date),
            )
        )

    def list_tasks_for_user(
        self, chat_id: int, user_id: int, task_date: str
    ) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT *
                FROM tasks
                WHERE chat_id = ? AND assignee_user_id = ? AND task_date = ?
                ORDER BY status, id
                """,
                (chat_id, user_id, task_date),
            )
        )

    def add_progress(
        self, chat_id: int, task_id: int, user_id: int, note: str, now: datetime
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO task_progress (task_id, chat_id, user_id, note, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (task_id, chat_id, user_id, note, now.isoformat(timespec="seconds")),
        )
        self.conn.commit()

    def mark_done(
        self,
        chat_id: int,
        task_id: int,
        user_id: int,
        note: str | None,
        now: datetime,
    ) -> None:
        if note:
            self.add_progress(chat_id, task_id, user_id, note, now)
        self.conn.execute(
            """
            UPDATE tasks
            SET status = 'done', completed_at = ?
            WHERE chat_id = ? AND id = ?
            """,
            (now.isoformat(timespec="seconds"), chat_id, task_id),
        )
        self.conn.commit()

    def attendance_summary(self, chat_id: int, record_date: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT
                    u.full_name,
                    u.username,
                    ar.user_id,
                    MAX(CASE WHEN ar.kind = 'sign' THEN ar.created_at END) AS sign_at,
                    MAX(CASE WHEN ar.kind = 'clock_in' THEN ar.created_at END) AS clock_in_at,
                    MAX(CASE WHEN ar.kind = 'clock_out' THEN ar.created_at END) AS clock_out_at
                FROM attendance_records ar
                LEFT JOIN users u
                    ON u.chat_id = ar.chat_id AND u.user_id = ar.user_id
                WHERE ar.chat_id = ? AND ar.record_date = ?
                GROUP BY ar.user_id
                ORDER BY u.full_name, ar.user_id
                """,
                (chat_id, record_date),
            )
        )

    def progress_for_tasks(self, task_ids: Iterable[int]) -> dict[int, list[sqlite3.Row]]:
        ids = list(task_ids)
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM task_progress
            WHERE task_id IN ({placeholders})
            ORDER BY created_at
            """,
            ids,
        ).fetchall()
        result: dict[int, list[sqlite3.Row]] = {task_id: [] for task_id in ids}
        for row in rows:
            result.setdefault(int(row["task_id"]), []).append(row)
        return result
