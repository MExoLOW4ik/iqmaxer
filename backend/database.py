"""
database.py — SQLite CRUD for iQmaxer

Tables: user, goals, quests, achievements
All functions return plain dicts/lists — no DB cursor leakage.
"""

import os
import sqlite3
from datetime import date, datetime, timedelta
from typing import Optional

from gamification import get_level, get_rank, level_up_benefits

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "iqmaxer.db")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    return dict(row)


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create tables and default user if they don't exist."""
    conn = _get_conn()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT DEFAULT 'Novice',
            level INTEGER DEFAULT 1,
            xp INTEGER DEFAULT 0,
            rank TEXT DEFAULT 'E',
            streak INTEGER DEFAULT 0,
            last_active_date TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            timeframe TEXT,
            target_date TEXT,
            rank TEXT DEFAULT 'E',
            total_xp INTEGER DEFAULT 0,
            earned_xp INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS quests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER REFERENCES goals(id),
            parent_id INTEGER REFERENCES quests(id),
            title TEXT NOT NULL,
            description TEXT,
            quest_type TEXT,
            rank TEXT DEFAULT 'E',
            xp_reward INTEGER DEFAULT 10,
            deadline TEXT,
            completed INTEGER DEFAULT 0,
            "order" INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            achievement_id TEXT UNIQUE,
            name TEXT,
            unlocked_at TEXT DEFAULT (datetime('now'))
        );
    """)

    # Seed default user if table is empty
    existing = cursor.execute("SELECT id FROM user WHERE id = 1").fetchone()
    if not existing:
        cursor.execute(
            "INSERT INTO user (id, name, level, xp, rank, streak) "
            "VALUES (1, 'Novice', 1, 0, 'E', 0)"
        )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

def get_user() -> dict:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM user WHERE id = 1").fetchone()
    conn.close()
    return _row_to_dict(row)


def update_streak() -> None:
    """Update streak based on last_active_date vs today."""
    conn = _get_conn()
    cursor = conn.cursor()

    user = cursor.execute("SELECT * FROM user WHERE id = 1").fetchone()
    if not user:
        conn.close()
        return

    today = date.today().isoformat()
    last_active = user["last_active_date"]

    if last_active == today:
        pass  # Already updated today
    elif last_active:
        last_date = datetime.strptime(last_active, "%Y-%m-%d").date()
        yesterday = date.today() - timedelta(days=1)

        if last_date == yesterday:
            cursor.execute(
                "UPDATE user SET streak = streak + 1, last_active_date = ? WHERE id = 1",
                (today,),
            )
        else:
            cursor.execute(
                "UPDATE user SET streak = 1, last_active_date = ? WHERE id = 1",
                (today,),
            )
    else:
        cursor.execute(
            "UPDATE user SET streak = 1, last_active_date = ? WHERE id = 1",
            (today,),
        )

    conn.commit()
    conn.close()


def update_user_xp(added_xp: int) -> dict:
    """Add XP, recalculate level/rank, and return summary.

    Returns::

        {level, xp, rank, level_up, benefits}
    """
    conn = _get_conn()
    cursor = conn.cursor()

    user = cursor.execute("SELECT * FROM user WHERE id = 1").fetchone()
    old_level = user["level"]
    new_xp = user["xp"] + added_xp

    new_level = get_level(new_xp)
    new_rank = get_rank(new_level)
    level_up = new_level > old_level

    cursor.execute(
        "UPDATE user SET xp = ?, level = ?, rank = ? WHERE id = 1",
        (new_xp, new_level, new_rank),
    )
    conn.commit()
    conn.close()

    return {
        "level": new_level,
        "xp": new_xp,
        "rank": new_rank,
        "level_up": level_up,
        "benefits": level_up_benefits(new_level) if level_up else {},
    }


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

def get_goals() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM goals ORDER BY created_at DESC").fetchall()
    conn.close()
    return _rows_to_dicts(rows)


def get_goal(goal_id: int) -> Optional[dict]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    conn.close()
    return _row_to_dict(row)


def create_goal(
    title: str,
    description: str,
    timeframe: str,
    target_date: str,
    generated_plan: dict,
) -> dict:
    """Create a goal and its generated quests in one transaction."""
    conn = _get_conn()
    cursor = conn.cursor()

    cursor.execute(
        "INSERT INTO goals (title, description, timeframe, target_date) VALUES (?, ?, ?, ?)",
        (title, description, timeframe, target_date),
    )
    goal_id = cursor.lastrowid

    quests_data = generated_plan.get("quests", [])
    for i, q in enumerate(quests_data):
        cursor.execute(
            """INSERT INTO quests
               (goal_id, title, description, quest_type, rank, xp_reward, deadline, "order")
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                goal_id,
                q.get("title", ""),
                q.get("description", ""),
                q.get("quest_type", "daily"),
                q.get("rank", "E"),
                q.get("xp_reward", 10),
                q.get("deadline"),
                q.get("order", i),
            ),
        )

    conn.commit()
    goal = _row_to_dict(
        cursor.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    )
    conn.close()
    return goal


# ---------------------------------------------------------------------------
# Quests
# ---------------------------------------------------------------------------

def get_quests(goal_id: int) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        'SELECT * FROM quests WHERE goal_id = ? ORDER BY "order" ASC',
        (goal_id,),
    ).fetchall()
    conn.close()
    return _rows_to_dicts(rows)


def get_daily_quests() -> list[dict]:
    """Today's incomplete daily quests."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT q.*, g.title AS goal_title
           FROM quests q
           LEFT JOIN goals g ON q.goal_id = g.id
           WHERE q.quest_type = 'daily' AND q.completed = 0
           ORDER BY q."order" ASC""",
    ).fetchall()
    conn.close()
    return _rows_to_dicts(rows)


def get_all_quests() -> list[dict]:
    """All quests with associated goal title."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT q.*, g.title AS goal_title
           FROM quests q
           LEFT JOIN goals g ON q.goal_id = g.id
           ORDER BY q.created_at DESC""",
    ).fetchall()
    conn.close()
    return _rows_to_dicts(rows)


def complete_quest(quest_id: int) -> dict:
    """Mark a quest completed and return quest data + base xp_reward.

    Returns ``{"quest": …, "xp_reward": …}`` or ``{"error": …}``.
    """
    conn = _get_conn()
    cursor = conn.cursor()

    quest = cursor.execute("SELECT * FROM quests WHERE id = ?", (quest_id,)).fetchone()
    if not quest:
        conn.close()
        return {"error": "Quest not found"}

    if quest["completed"]:
        conn.close()
        return {"error": "Quest already completed"}

    cursor.execute("UPDATE quests SET completed = 1 WHERE id = ?", (quest_id,))

    # Accumulate XP to parent goal
    goal_id = quest["goal_id"]
    if goal_id:
        cursor.execute(
            "UPDATE goals SET earned_xp = earned_xp + ? WHERE id = ?",
            (quest["xp_reward"], goal_id),
        )

    conn.commit()

    quest_dict = _row_to_dict(quest)
    quest_dict["completed"] = 1
    conn.close()

    return {"quest": quest_dict, "xp_reward": quest_dict["xp_reward"]}


def reset_daily_quests() -> None:
    """Reset completion flag on daily quests (called at start of new day)."""
    conn = _get_conn()
    conn.execute(
        "UPDATE quests SET completed = 0 WHERE quest_type = 'daily' AND completed = 1"
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Achievements
# ---------------------------------------------------------------------------

def get_achievements() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM achievements ORDER BY unlocked_at ASC").fetchall()
    conn.close()
    return _rows_to_dicts(rows)


def add_achievement(achievement_id: str, name: str) -> bool:
    """Insert a new achievement record. Returns False if already unlocked."""
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO achievements (achievement_id, name) VALUES (?, ?)",
            (achievement_id, name),
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def get_history() -> list[dict]:
    """Return the 50 most recently completed quests."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT q.*, g.title AS goal_title
           FROM quests q
           LEFT JOIN goals g ON q.goal_id = g.id
           WHERE q.completed = 1
           ORDER BY q.created_at DESC
           LIMIT 50""",
    ).fetchall()
    conn.close()
    return _rows_to_dicts(rows)
