"""
database.py — SQLite CRUD for iQmaxer

Tables: user, goals, quests, achievements
All functions return plain dicts/lists — no DB cursor leakage.
"""

import os
import sqlite3
from datetime import date, datetime, timedelta
from typing import Optional
import random
import json

from gamification import get_level, get_rank, level_up_benefits

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "iqmaxer.db")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Add busy timeout to prevent DB locking issues
    conn.execute("PRAGMA busy_timeout=5000")
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

        CREATE TABLE IF NOT EXISTS materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            subject TEXT,
            difficulty_rank TEXT DEFAULT 'E',
            xp_reward INTEGER DEFAULT 20,
            studied INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            subject TEXT,
            difficulty_rank TEXT DEFAULT 'E',
            pass_percent INTEGER DEFAULT 70,
            xp_reward INTEGER DEFAULT 50,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS test_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER REFERENCES tests(id),
            question TEXT NOT NULL,
            options TEXT NOT NULL,
            correct_index INTEGER NOT NULL,
            "order" INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS test_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER REFERENCES tests(id),
            score INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0,
            passed INTEGER DEFAULT 0,
            completed_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            subject TEXT,
            difficulty_rank TEXT DEFAULT 'E',
            xp_reward INTEGER DEFAULT 30,
            status TEXT DEFAULT 'pending',
            deadline TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS ai_courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            difficulty TEXT DEFAULT 'E',
            status TEXT DEFAULT 'generating',  -- generating, ready, error
            total_xp INTEGER DEFAULT 0,
            earned_xp INTEGER DEFAULT 0,
            error_message TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS ai_quests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER REFERENCES ai_courses(id),
            title TEXT NOT NULL,
            story TEXT NOT NULL,           -- the vivid story/scenario
            topic TEXT,                    -- what concept this teaches
            difficulty_rank TEXT DEFAULT 'E',
            xp_reward INTEGER DEFAULT 20,
            correct_answer TEXT NOT NULL,  -- answer key
            hint TEXT,                     -- AI-generated hint
            completed INTEGER DEFAULT 0,
            attempts INTEGER DEFAULT 0,
            correct_attempts INTEGER DEFAULT 0,
            "order" INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS ai_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quest_id INTEGER REFERENCES ai_quests(id),
            user_answer TEXT NOT NULL,
            is_correct INTEGER DEFAULT 0,
            feedback TEXT,                  -- AI feedback on the answer
            attempted_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS tutor_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            title TEXT,                     -- short display title (defaults to topic)
            mastery INTEGER DEFAULT 0,      -- 0..100, per-topic progress
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS tutor_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER REFERENCES tutor_sessions(id),
            role TEXT NOT NULL,             -- 'user' | 'assistant' | 'system'
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
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

    # Seed AI courses if empty
    seed_ai_courses()


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


# ---------------------------------------------------------------------------
# Helper: deterministic shuffle for test questions
# ---------------------------------------------------------------------------

def _shuffle_question(options_str: str, correct_index: int, seed: int):
    """Shuffle options deterministically by seed (question id).

    Returns (shuffled_options: list[str], shuffled_correct_index: int).
    """
    options = json.loads(options_str)
    rng = random.Random(str(seed))
    indexed = list(enumerate(options))
    rng.shuffle(indexed)
    shuffled = [opt for _, opt in indexed]
    # Find where the correct answer landed in the shuffled list
    shuffled_correct = next(i for i, (orig, _) in enumerate(indexed) if orig == correct_index)
    return shuffled, shuffled_correct


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

def get_materials() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM materials ORDER BY created_at DESC").fetchall()
    conn.close()
    return _rows_to_dicts(rows)


def get_material(material_id: int) -> Optional[dict]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
    conn.close()
    return _row_to_dict(row)


def study_material(material_id: int) -> dict:
    """Mark material as studied and award XP (once only)."""
    conn = _get_conn()
    cursor = conn.cursor()

    material = cursor.execute(
        "SELECT * FROM materials WHERE id = ?", (material_id,)
    ).fetchone()
    if not material:
        conn.close()
        return {"error": "Material not found"}

    if material["studied"]:
        conn.close()
        return {"error": "Material already studied"}

    cursor.execute("UPDATE materials SET studied = 1 WHERE id = ?", (material_id,))
    conn.commit()

    material_dict = _row_to_dict(
        cursor.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
    )
    conn.close()

    # Award XP (separate connection)
    xp_reward = material["xp_reward"]
    xp_result = update_user_xp(xp_reward)

    return {
        "material": material_dict,
        "xp_earned": xp_reward,
        "xp_result": xp_result,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def get_tests() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM tests ORDER BY created_at DESC").fetchall()
    conn.close()
    return _rows_to_dicts(rows)


def get_test_with_questions(test_id: int) -> Optional[dict]:
    """Return test dict with questions (options shuffled, correct_index removed)."""
    conn = _get_conn()
    test = conn.execute("SELECT * FROM tests WHERE id = ?", (test_id,)).fetchone()
    if not test:
        conn.close()
        return None

    test_dict = _row_to_dict(test)
    question_rows = conn.execute(
        'SELECT * FROM test_questions WHERE test_id = ? ORDER BY "order" ASC',
        (test_id,),
    ).fetchall()
    conn.close()

    questions = []
    for qr in question_rows:
        q = _row_to_dict(qr)
        shuffled_opts, _ = _shuffle_question(q["options"], q["correct_index"], q["id"])
        questions.append({
            "id": q["id"],
            "question": q["question"],
            "options": shuffled_opts,
            "order": q["order"],
        })

    test_dict["questions"] = questions
    return test_dict


def submit_test(test_id: int, answers: list[int]) -> dict:
    """Score answers, record result, award XP if passed."""
    conn = _get_conn()
    cursor = conn.cursor()

    test = cursor.execute("SELECT * FROM tests WHERE id = ?", (test_id,)).fetchone()
    if not test:
        conn.close()
        return {"error": "Test not found"}

    question_rows = cursor.execute(
        'SELECT * FROM test_questions WHERE test_id = ? ORDER BY "order" ASC',
        (test_id,),
    ).fetchall()

    if len(answers) != len(question_rows):
        conn.close()
        return {"error": "Answer count mismatch"}

    score = 0
    total = len(question_rows)

    for qr, chosen in zip(question_rows, answers):
        q = _row_to_dict(qr)
        _, shuffled_correct = _shuffle_question(q["options"], q["correct_index"], q["id"])
        if chosen == shuffled_correct:
            score += 1

    pass_percent = test["pass_percent"]
    pct = (score / total) * 100 if total > 0 else 0
    passed = 1 if pct >= pass_percent else 0

    cursor.execute(
        "INSERT INTO test_results (test_id, score, total, passed) VALUES (?, ?, ?, ?)",
        (test_id, score, total, passed),
    )
    result_id = cursor.lastrowid

    conn.commit()
    conn.close()

    # Award XP if passed (separate connection)
    xp_result = None
    xp_earned = 0
    if passed:
        xp_earned = test["xp_reward"]
        xp_result = update_user_xp(xp_earned)

    return {
        "result_id": result_id,
        "score": score,
        "total": total,
        "percentage": round(pct, 1),
        "passed": bool(passed),
        "xp_earned": xp_earned,
        "xp_result": xp_result,
    }


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------

def get_assignments() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM assignments ORDER BY created_at DESC").fetchall()
    conn.close()
    return _rows_to_dicts(rows)


def create_assignment(
    title: str,
    description: str = "",
    subject: str = "",
    difficulty_rank: str = "E",
    xp_reward: int = 30,
    deadline: str = "",
) -> dict:
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO assignments (title, description, subject, difficulty_rank, xp_reward, deadline) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (title, description, subject, difficulty_rank, xp_reward, deadline or None),
    )
    assignment_id = cursor.lastrowid
    conn.commit()
    assignment = _row_to_dict(
        cursor.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
    )
    conn.close()
    return assignment


def complete_assignment(assignment_id: int) -> dict:
    """Mark assignment completed and award XP (once only)."""
    conn = _get_conn()
    cursor = conn.cursor()

    assignment = cursor.execute(
        "SELECT * FROM assignments WHERE id = ?", (assignment_id,)
    ).fetchone()
    if not assignment:
        conn.close()
        return {"error": "Assignment not found"}

    if assignment["status"] != "pending":
        conn.close()
        return {"error": "Assignment already completed"}

    cursor.execute(
        "UPDATE assignments SET status = 'completed' WHERE id = ?", (assignment_id,)
    )
    conn.commit()

    assignment_dict = _row_to_dict(
        cursor.execute(
            "SELECT * FROM assignments WHERE id = ?", (assignment_id,)
        ).fetchone()
    )
    conn.close()

    # Award XP (separate connection)
    xp_reward = assignment["xp_reward"]
    xp_result = update_user_xp(xp_reward)

    return {
        "assignment": assignment_dict,
        "xp_earned": xp_reward,
        "xp_result": xp_result,
    }


# ---------------------------------------------------------------------------
# AI Courses
# ---------------------------------------------------------------------------


def create_course(goal: str, title: str, description: str = "", difficulty: str = "E") -> dict:
    """Create a new AI-generated course. Returns the course dict."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO ai_courses (goal, title, description, difficulty, status) VALUES (?, ?, ?, ?, 'generating')",
        (goal, title, description, difficulty),
    )
    course_id = cursor.lastrowid
    conn.commit()
    course = _row_to_dict(
        cursor.execute("SELECT * FROM ai_courses WHERE id = ?", (course_id,)).fetchone()
    )
    conn.close()
    return course


def get_courses() -> list[dict]:
    """List all AI courses with stats."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT c.*,
            (SELECT COUNT(*) FROM ai_quests WHERE course_id = c.id) as total_quests,
            (SELECT COUNT(*) FROM ai_quests WHERE course_id = c.id AND completed = 1) as completed_quests
           FROM ai_courses c
           ORDER BY c.created_at DESC"""
    ).fetchall()
    conn.close()
    return _rows_to_dicts(rows)


def get_course(course_id: int) -> Optional[dict]:
    """Get a course with all its quests (hides correct_answer for non-completed quests)."""
    conn = _get_conn()
    course = conn.execute("SELECT * FROM ai_courses WHERE id = ?", (course_id,)).fetchone()
    if not course:
        conn.close()
        return None

    course_dict = _row_to_dict(course)
    quest_rows = conn.execute(
        'SELECT * FROM ai_quests WHERE course_id = ? ORDER BY "order" ASC',
        (course_id,),
    ).fetchall()
    conn.close()

    quests = []
    for qr in quest_rows:
        q = _row_to_dict(qr)
        # Hide correct_answer unless quest is completed
        if not q["completed"]:
            q.pop("correct_answer", None)
        quests.append(q)

    course_dict["quests"] = quests
    return course_dict


def add_quest(course_id: int, quest_data: dict) -> dict:
    """Add a quest to a course. Returns the quest dict."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO ai_quests
           (course_id, title, story, topic, difficulty_rank, xp_reward, correct_answer, hint, "order")
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            course_id,
            quest_data.get("title", ""),
            quest_data.get("story", ""),
            quest_data.get("topic", ""),
            quest_data.get("difficulty_rank", "E"),
            quest_data.get("xp_reward", 20),
            quest_data.get("correct_answer", ""),
            quest_data.get("hint", ""),
            quest_data.get("order", 0),
        ),
    )
    quest_id = cursor.lastrowid

    # Update course total_xp
    cursor.execute(
        "UPDATE ai_courses SET total_xp = total_xp + ? WHERE id = ?",
        (quest_data.get("xp_reward", 20), course_id),
    )

    conn.commit()
    quest = _row_to_dict(
        cursor.execute("SELECT * FROM ai_quests WHERE id = ?", (quest_id,)).fetchone()
    )
    conn.close()
    return quest


def get_quest(quest_id: int) -> Optional[dict]:
    """Get a single quest by ID."""
    conn = _get_conn()
    quest = conn.execute("SELECT * FROM ai_quests WHERE id = ?", (quest_id,)).fetchone()
    conn.close()
    return _row_to_dict(quest)


def complete_quest(quest_id: int, user_answer: str, is_correct: bool, feedback: str) -> dict:
    """Record an attempt and optionally mark quest completed. Returns result dict."""
    conn = _get_conn()
    cursor = conn.cursor()

    quest = cursor.execute("SELECT * FROM ai_quests WHERE id = ?", (quest_id,)).fetchone()
    if not quest:
        conn.close()
        return {"error": "Quest not found"}

    # Record the attempt
    cursor.execute(
        "INSERT INTO ai_attempts (quest_id, user_answer, is_correct, feedback) VALUES (?, ?, ?, ?)",
        (quest_id, user_answer, 1 if is_correct else 0, feedback),
    )

    # Update quest stats
    cursor.execute(
        "UPDATE ai_quests SET attempts = attempts + 1, correct_attempts = correct_attempts + ? WHERE id = ?",
        (1 if is_correct else 0, quest_id),
    )

    xp_earned = 0
    if is_correct and not quest["completed"]:
        # Mark completed and award XP
        xp_earned = quest["xp_reward"]
        cursor.execute("UPDATE ai_quests SET completed = 1 WHERE id = ?", (quest_id,))
        # Update course earned_xp
        cursor.execute(
            "UPDATE ai_courses SET earned_xp = earned_xp + ? WHERE id = ?",
            (xp_earned, quest["course_id"]),
        )

    conn.commit()
    conn.close()

    return {
        "is_correct": is_correct,
        "feedback": feedback,
        "xp_earned": xp_earned,
    }


def update_course_status(course_id: int, status: str, error_message: str = None) -> dict:
    """Update course generation status."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE ai_courses SET status = ?, error_message = ? WHERE id = ?",
        (status, error_message, course_id),
    )
    conn.commit()
    course = _row_to_dict(
        cursor.execute("SELECT * FROM ai_courses WHERE id = ?", (course_id,)).fetchone()
    )
    conn.close()
    return course


def get_course_stats(course_id: int) -> dict:
    """Get completion stats for a course."""
    conn = _get_conn()
    row = conn.execute(
        """SELECT
            COUNT(*) as total,
            SUM(CASE WHEN completed = 1 THEN 1 ELSE 0 END) as completed,
            SUM(xp_reward) as total_xp
           FROM ai_quests WHERE course_id = ?""",
        (course_id,),
    ).fetchone()
    conn.close()
    if not row:
        return {"total": 0, "completed": 0, "total_xp": 0}
    result = dict(row)
    return {
        "total": result["total"] or 0,
        "completed": result["completed"] or 0,
        "total_xp": result["total_xp"] or 0,
    }


# ---------------------------------------------------------------------------
# Seed AI Courses
# ---------------------------------------------------------------------------


def seed_ai_courses() -> None:
    """Seed 1-2 example courses with hardcoded quests for testing without API key."""
    conn = _get_conn()
    cursor = conn.cursor()

    # Check if already seeded
    existing = cursor.execute("SELECT COUNT(*) as cnt FROM ai_courses").fetchone()
    if existing and existing["cnt"] > 0:
        conn.close()
        return

    # Example Course 1: Physics of Extreme Environments
    cursor.execute(
        "INSERT INTO ai_courses (goal, title, description, difficulty, status, total_xp, earned_xp) "
        "VALUES (?, ?, ?, ?, 'ready', ?, 0)",
        (
            "Learn physics through extreme environment problems",
            "Physics of Extreme Environments",
            "Master physics by solving practical problems on Mars, deep ocean, and other extreme locations. "
            "A friendly adventure course where you're an explorer navigating hostile environments.",
            "E", 240,
        ),
    )
    course1_id = cursor.lastrowid

    quests1 = [
        {
            "title": "The Mars Oil Barrel Launch",
            "story": "You are an explorer on Mars. A supply drop of a 100kg oil barrel must be launched from Point A (altitude 0m) to Point B (altitude 50m) which is 200m away horizontally. Mars gravity is 3.72 m/s². There is a headwind of 10 m/s opposing the launch. Assuming no air resistance on the barrel itself (only wind affects horizontal velocity), calculate the minimum initial velocity (in m/s) required to reach Point B. Round to 1 decimal place.",
            "topic": "Projectile Motion with Modified Gravity",
            "difficulty_rank": "D",
            "xp_reward": 30,
            "correct_answer": "32.5",
            "hint": "Use the projectile range equation: R = v²sin(2θ)/g. Since launch angle isn't specified, assume the optimal 45° for minimum velocity. Account for wind reducing horizontal component.",
        },
        {
            "title": "Deep Ocean Pressure Vessel",
            "story": "You descend to the Mariana Trench (11,000m deep) in a submersible. The viewport is a circular window of radius 0.3m. Seawater density is 1025 kg/m³. Calculate the total force (in Newtons) exerted by water on the viewport at that depth. Use g = 9.81 m/s². Round to the nearest whole number.",
            "topic": "Hydrostatic Pressure and Force",
            "difficulty_rank": "D",
            "xp_reward": 30,
            "correct_answer": "31286976",
            "hint": "Pressure at depth: P = ρgh. Force on a surface: F = P × A. The viewport is a circle: A = πr².",
        },
        {
            "title": "Venus Sulfuric Acid Cloud Navigator",
            "story": "Your explorer ship flies through Venus's upper atmosphere at 100 km altitude where temperature is -10°C and pressure is 10,000 Pa. The atmosphere is 96.5% CO₂ (molar mass 44 g/mol) and 3.5% N₂ (28 g/mol). Calculate the density of the atmosphere (in kg/m³) at this altitude. Use R = 8.314 J/(mol·K). Round to 3 decimal places.",
            "topic": "Ideal Gas Law with Mixed Gases",
            "difficulty_rank": "C",
            "xp_reward": 50,
            "correct_answer": "0.188",
            "hint": "Use the ideal gas law: PV = nRT. But you need density ρ = m/V = (n×M_avg)/V. First find the average molar mass of the mixture. Then use ρ = (P × M_avg) / (R × T). Remember to convert °C to Kelvin.",
        },
        {
            "title": "Asteroid Mining: Kinetic Energy",
            "story": "You're an explorer mining an asteroid of mass 5×10^12 kg approaching Earth at 15 km/s relative velocity. Your ship must deflect it by applying a force of 10^6 N. If the force is applied continuously for 30 days, will this be enough to stop it? Calculate the asteroid's kinetic energy in Joules (scientific notation) and determine if the force applied over the given time provides enough work to stop it.",
            "topic": "Kinetic Energy and Work-Energy Theorem",
            "difficulty_rank": "C",
            "xp_reward": 50,
            "correct_answer": "5.625e20",
            "hint": "Kinetic energy = ½mv². Work = Force × distance. To find distance, use constant acceleration: v_f² = v_i² + 2ad, where a = F/m. Or just check if impulse (F×t) equals momentum change needed.",
        },
        {
            "title": "Space Station Orbit Calculation",
            "story": "The International Space Station orbits at 408 km above Earth's surface. Earth's radius is 6371 km, mass is 5.97×10^24 kg. Calculate the orbital velocity in km/s. Use G = 6.67×10^-11 N·m²/kg². Round to 2 decimal places.",
            "topic": "Orbital Mechanics",
            "difficulty_rank": "B",
            "xp_reward": 80,
            "correct_answer": "7.66",
            "hint": "For a circular orbit, centripetal force = gravitational force: mv²/r = GMm/r². Solve for v: v = √(GM/r). Remember r = Earth radius + orbital altitude. Convert to km for answer.",
        },
    ]

    for i, q in enumerate(quests1):
        cursor.execute(
            """INSERT INTO ai_quests
               (course_id, title, story, topic, difficulty_rank, xp_reward, correct_answer, hint, "order")
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (course1_id, q["title"], q["story"], q["topic"], q["difficulty_rank"],
             q["xp_reward"], q["correct_answer"], q["hint"], i),
        )

    # Example Course 2: Chemistry of Magical Elements
    cursor.execute(
        "INSERT INTO ai_courses (goal, title, description, difficulty, status, total_xp, earned_xp) "
        "VALUES (?, ?, ?, ?, 'ready', ?, 0)",
        (
            "Master chemistry through magical world scenarios",
            "Chemistry of Magical Elements",
            "Learn chemistry concepts through magical world problems. As an explorer-alchemist, you mix potions, transmute elements, and balance magical reactions.",
            "E", 180,
        ),
    )
    course2_id = cursor.lastrowid

    quests2 = [
        {
            "title": "Potion Concentration",
            "story": "You need to brew a healing potion that is 12% (by volume) unicorn essence. You have 500 mL of a 5% essence solution and pure (100%) essence. How many mL of pure essence must you add to reach the target 12% concentration? Round to 1 decimal place.",
            "topic": "Solution Concentration (Mixture Problems)",
            "difficulty_rank": "E",
            "xp_reward": 20,
            "correct_answer": "39.8",
            "hint": "Let x be the mL of pure essence added. Total volume = 500 + x. Amount of essence = 0.05×500 + 1.0×x. Set this equal to 0.12×(500 + x) and solve for x.",
        },
        {
            "title": "Mana Crystal Stoichiometry",
            "story": "Mana crystal (MgSO₄·7H₂O) is used to power enchantments. Calculate the percentage (by mass) of water in the hydrated crystal. Atomic masses: Mg=24.3, S=32.1, O=16.0, H=1.0. Round to 1 decimal place.",
            "topic": "Stoichiometry, Percent Composition",
            "difficulty_rank": "E",
            "xp_reward": 20,
            "correct_answer": "51.1",
            "hint": "Calculate formula mass of MgSO₄·7H₂O. The water part is 7 × (2×1.0 + 16.0). Then (mass of water / total formula mass) × 100%.",
        },
        {
            "title": "Fire Potion Exothermic Reaction",
            "story": "A fire potion uses the reaction: 2Al + Fe₂O₃ → 2Fe + Al₂O₃ (thermite reaction). Given bond energies: Al-O=512 kJ/mol, Fe-O=390 kJ/mol, Al—Al=200 kJ/mol, Fe—Fe=150 kJ/mol, O=O=498 kJ/mol. Calculate the approximate enthalpy change (ΔH) in kJ for this reaction (not per mole). Round to the nearest whole number.",
            "topic": "Thermochemistry, Bond Enthalpies",
            "difficulty_rank": "D",
            "xp_reward": 30,
            "correct_answer": "-852",
            "hint": "ΔH = energy of bonds broken - energy of bonds formed. Break: 2×(Al-Al) + 2×(Fe-O)... Actually for thermite: break bonds in reactants = 2×(?) + 3×(?). Form bonds in products = 2×(?) + 3×(?).",
        },
        {
            "title": "Mana Elixir pH Balance",
            "story": "A mana elixir has [H⁺] = 3.16×10⁻⁶ M. What is its pH? If the elixir must be between pH 5.0 and 5.5 for safe consumption, is it safe? Provide the pH value rounded to 2 decimal places, followed by 'yes' or 'no' (e.g. '5.50,yes').",
            "topic": "pH Calculations",
            "difficulty_rank": "C",
            "xp_reward": 50,
            "correct_answer": "5.50,yes",
            "hint": "pH = -log₁₀[H⁺]. Use log₁₀(3.16×10⁻⁶) = log₁₀(3.16) + log₁₀(10⁻⁶).",
        },
        {
            "title": "Philosopher's Stone: Half-Life",
            "story": "A Philosopher's Stone contains a magical isotope with half-life of 12.5 years. You find a stone with 25% of its original magical potency remaining. How old is the stone in years? Round to 1 decimal place.",
            "topic": "Radioactive Decay, Half-Life",
            "difficulty_rank": "B",
            "xp_reward": 60,
            "correct_answer": "25.0",
            "hint": "After n half-lives, remaining fraction = (½)^n. Set (½)^n = 0.25 and solve for n. Then multiply by the half-life.",
        },
    ]

    for i, q in enumerate(quests2):
        cursor.execute(
            """INSERT INTO ai_quests
               (course_id, title, story, topic, difficulty_rank, xp_reward, correct_answer, hint, "order")
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (course2_id, q["title"], q["story"], q["topic"], q["difficulty_rank"],
             q["xp_reward"], q["correct_answer"], q["hint"], i),
        )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Tutor sessions (conversational learning)
# ---------------------------------------------------------------------------

def get_tutor_sessions() -> list[dict]:
    """List all tutor sessions, most recently updated first (for the sidebar)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM tutor_sessions ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return _rows_to_dicts(rows)


def create_tutor_session(topic: str, title: str = "") -> dict:
    """Create a new tutor session for a topic. Returns the session row."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO tutor_sessions (topic, title) VALUES (?, ?)",
        (topic, title or topic),
    )
    session_id = cursor.lastrowid
    conn.commit()
    row = conn.execute(
        "SELECT * FROM tutor_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    conn.close()
    return _row_to_dict(row)


def get_tutor_session(session_id: int) -> Optional[dict]:
    """Get a session plus its full message history (ordered oldest first)."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM tutor_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not row:
        conn.close()
        return None
    msgs = conn.execute(
        "SELECT * FROM tutor_messages WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    ).fetchall()
    conn.close()
    session = _row_to_dict(row)
    session["messages"] = _rows_to_dicts(msgs)
    return session


def get_tutor_messages(session_id: int) -> list[dict]:
    """Return just the message history (role/content) for an LLM call."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT role, content FROM tutor_messages WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    ).fetchall()
    conn.close()
    return _rows_to_dicts(rows)


def add_tutor_message(session_id: int, role: str, content: str) -> dict:
    """Append a message to a session and bump the session's updated_at."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO tutor_messages (session_id, role, content) VALUES (?, ?, ?)",
        (session_id, role, content),
    )
    msg_id = cursor.lastrowid
    cursor.execute(
        "UPDATE tutor_sessions SET updated_at = datetime('now') WHERE id = ?",
        (session_id,),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM tutor_messages WHERE id = ?", (msg_id,)
    ).fetchone()
    conn.close()
    return _row_to_dict(row)


def bump_tutor_mastery(session_id: int, amount: int) -> int:
    """Increase a session's mastery (0..100) by `amount`, capped. Returns new value."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE tutor_sessions SET mastery = MIN(100, mastery + ?) WHERE id = ?",
        (amount, session_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT mastery FROM tutor_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    conn.close()
    return row["mastery"] if row else 0


def delete_tutor_session(session_id: int) -> bool:
    """Delete a session and all its messages. Returns True if it existed."""
    conn = _get_conn()
    cursor = conn.cursor()
    existed = cursor.execute(
        "SELECT id FROM tutor_sessions WHERE id = ?", (session_id,)
    ).fetchone() is not None
    cursor.execute("DELETE FROM tutor_messages WHERE session_id = ?", (session_id,))
    cursor.execute("DELETE FROM tutor_sessions WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()
    return existed
