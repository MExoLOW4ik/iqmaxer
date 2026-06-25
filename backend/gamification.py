"""
gamification.py — iQmaxer Solo Leveling RPG Engine
===================================================

Pure XP, level, rank, streak, and achievement logic.
All functions are stateless (no I/O); the FastAPI caller
handles persistence and user state.

Rank system (Solo Leveling inspired):
    E  (Levels 1-3)
    D  (Levels 4-6)
    C  (Levels 7-9)
    B  (Levels 10-12)
    A  (Levels 13-15)
    S  (Levels 16+)

Level progression:
    XP needed to reach level N = sum(100 * i for i in range(1, N))
        Level 1:      0 XP
        Level 2:    100 XP
        Level 3:    300 XP  (100 + 200)
        Level 4:    600 XP  (100 + 200 + 300)
        Level n:  100 * n * (n - 1) / 2
"""

from __future__ import annotations

import math
from typing import Any

# ---------------------------------------------------------------------------
# Rank
# ---------------------------------------------------------------------------

RANK_TABLE: list[tuple[int, int, str]] = [
    (1, 3, "E"),
    (4, 6, "D"),
    (7, 9, "C"),
    (10, 12, "B"),
    (13, 15, "A"),
]

RANK_ORDER = ["E", "D", "C", "B", "A", "S"]


def get_rank(level: int) -> str:
    """Return the rank letter for a given level.

    Levels 1-3 → E, 4-6 → D, 7-9 → C, 10-12 → B, 13-15 → A, 16+ → S.
    """
    for lo, hi, rank in RANK_TABLE:
        if lo <= level <= hi:
            return rank
    return "S"  # level >= 16


# ---------------------------------------------------------------------------
# XP / Level
# ---------------------------------------------------------------------------

# XP needed to go from *level* to *level + 1*
# (not cumulative; that's handled by get_level / xp_for_next_level)
_XP_PER_LEVEL_FACTOR = 100


def _cumulative_xp(level: int) -> int:
    """Total XP required to reach *level* (1-indexed).

    Formula: 100 * level * (level - 1) / 2
    """
    if level <= 1:
        return 0
    return _XP_PER_LEVEL_FACTOR * level * (level - 1) // 2


def get_level(xp: int) -> int:
    """Calculate the current level from total XP earned.

    Uses the inverse of the cumulative XP formula.
    Level 1 requires 0 XP; each subsequent level costs
    ``100 * level`` XP over the previous.
    """
    if xp <= 0:
        return 1

    # Solve  n² - n - 2*xp/100 = 0  for the largest n where cumulative ≤ xp
    discriminant = 1 + 8 * xp // _XP_PER_LEVEL_FACTOR
    n = int((1 + math.isqrt(discriminant)) // 2)

    # Edge guard — floating edge cases on very small values
    while _cumulative_xp(n + 1) <= xp:
        n += 1
    while _cumulative_xp(n) > xp and n > 1:
        n -= 1

    return n


def xp_for_next_level(level: int) -> int:
    """Return the amount of XP needed to go from ``level`` to ``level + 1``.

    For level L, the cost is ``100 * L``.
    """
    if level < 1:
        level = 1
    return _XP_PER_LEVEL_FACTOR * level


def xp_progress(xp: int) -> dict[str, int]:
    """Convenience: return current-level XP progress info.

    Returns ``{"current_xp": …, "level": …, "xp_for_next": …, "xp_in_level": …,
    "xp_needed": …}``.
    """
    level = get_level(xp)
    xp_floor = _cumulative_xp(level)
    xp_next = xp_for_next_level(level)
    return {
        "current_xp": xp,
        "level": level,
        "xp_for_next": xp_next,
        "xp_in_level": xp - xp_floor,
        "xp_needed": xp_next - (xp - xp_floor),
        "rank": get_rank(level),
    }


# ---------------------------------------------------------------------------
# Quest rewards
# ---------------------------------------------------------------------------

_QUEST_BASE_XP: dict[str, int] = {
    "E": 10,
    "D": 25,
    "C": 50,
    "B": 100,
    "A": 250,
    "S": 500,
}


def quest_xp_reward(rank: str) -> int:
    """Base XP reward for completing a quest of the given rank.

    Ranks map as follows:
        E → 10,  D → 25,  C → 50,  B → 100,  A → 250,  S → 500

    Frequency multipliers (applied by the caller, but provided here
    for reference): daily → 1×, monthly → 3×, yearly → 10×.
    """
    return _QUEST_BASE_XP.get(rank.upper(), 10)


# ---------------------------------------------------------------------------
# Streak bonuses
# ---------------------------------------------------------------------------

_STREAK_BONUS_TABLE: list[tuple[int, float]] = [
    (1, 1.0),
    (4, 1.5),
    (8, 2.0),
    (15, 3.0),
    (31, 5.0),
]


def calculate_streak_bonus(streak_days: int) -> float:
    """Return the XP multiplier earned for a current streak.

    1-3   days → 1.0×
    4-7   days → 1.5×
    8-14  days → 2.0×
    15-30 days → 3.0×
    31+   days → 5.0×
    """
    if streak_days <= 0:
        return 1.0
    for threshold, multiplier in reversed(_STREAK_BONUS_TABLE):
        if streak_days >= threshold:
            return multiplier
    return 1.0


# ---------------------------------------------------------------------------
# Level-up benefits
# ---------------------------------------------------------------------------

_LEVEL_BENEFITS: list[tuple[int, str, list[str], str]] = [
    (
        1,
        "Novice",
        [],
        "The journey begins. Complete quests to earn XP and grow stronger.",
    ),
    (
        3,
        "Apprentice",
        ["extra_goal_slot"],
        "You've proven your determination. Unlock an additional goal slot.",
    ),
    (
        5,
        "Fighter",
        ["daily_quest_limit_10"],
        "Your daily capacity expands. You may now take on 10 daily quests.",
    ),
    (
        7,
        "Knight",
        ["streak_multiplier_1_5x"],
        "Your consistency is recognised. Streak multiplier starts at 1.5×.",
    ),
    (
        10,
        "Elite",
        ["extra_goal_slot", "extra_goal_slot_2"],
        "A rising force. Two more goal slots unlocked.",
    ),
    (
        13,
        "Master",
        ["double_xp_all_quests"],
        "All quests now grant 2× base XP. Your power is undeniable.",
    ),
    (
        16,
        "Grandmaster",
        ["title_color_change"],
        "Your title shimmers with a new hue — a sign of true mastery.",
    ),
    (
        20,
        "Shadow Monarch",
        [
            "max_goal_slots",
            "max_daily_quests",
            "special_title",
            "double_xp_all_quests",
        ],
        "The pinnacle of power. All benefits unlocked. You are the Shadow Monarch.",
    ),
]


def level_up_benefits(level: int) -> dict[str, Any]:
    """Return the title, unlock list, and description for the given level.

    The caller should merge these with the user's existing unlocks.
    """
    title = "Novice"
    unlocks: list[str] = []
    description = _LEVEL_BENEFITS[0][3]

    for lvl, t, u, desc in _LEVEL_BENEFITS:
        if level >= lvl:
            title = t
            unlocks = list(u)
            description = desc

    return {
        "title": title,
        "unlocks": unlocks,
        "description": description,
    }


# ---------------------------------------------------------------------------
# Achievements
# ---------------------------------------------------------------------------

_ACHIEVEMENT_DEFS: dict[str, dict[str, Any]] = {
    "first_steps": {
        "id": "first_steps",
        "name": "First Steps",
        "description": "Complete your first quest.",
        "icon": "👣",
        "xp_bonus": 50,
    },
    "dedicated": {
        "id": "dedicated",
        "name": "Dedicated",
        "description": "Maintain a 7-day streak.",
        "icon": "🔥",
        "xp_bonus": 200,
    },
    "scholar": {
        "id": "scholar",
        "name": "Scholar",
        "description": "Complete 10 study-type quests.",
        "icon": "📚",
        "xp_bonus": 300,
    },
    "grinder": {
        "id": "grinder",
        "name": "Grinder",
        "description": "Complete 30 daily quests.",
        "icon": "⚙️",
        "xp_bonus": 500,
    },
    "rising_star": {
        "id": "rising_star",
        "name": "Rising Star",
        "description": "Reach level 5.",
        "icon": "⭐",
        "xp_bonus": 400,
    },
    "shadow": {
        "id": "shadow",
        "name": "Shadow",
        "description": "Reach level 20.",
        "icon": "🌑",
        "xp_bonus": 2000,
    },
    "conqueror": {
        "id": "conqueror",
        "name": "Conqueror",
        "description": "Complete 5 goals.",
        "icon": "🏆",
        "xp_bonus": 1000,
    },
    "s_rank_hunter": {
        "id": "s_rank_hunter",
        "name": "S-Rank Hunter",
        "description": "Complete an S-rank quest.",
        "icon": "👑",
        "xp_bonus": 1500,
    },
}


def check_achievements(completed_quests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Evaluate completed quest data and return a list of newly earned achievements.

    ``completed_quests`` should be a list of dicts where each dict may contain:
        - ``type`` / ``quest_type`` (str): e.g. ``"daily"``, ``"study"``, ``"monthly"``
        - ``rank`` (str): ``"E"`` … ``"S"``
        - ``streak_days`` (int): current streak length when quest was completed
        - ``goal_id`` (str): which goal this quest belongs to (if any)
        - ``user_level`` (int, optional): user level at the time of this quest

    Returns a list of achievement dicts with the same keys as the definitions
    (``id``, ``name``, ``description``, ``icon``, ``xp_bonus``).

    **Note**: Level-based achievements (Rising Star, Shadow) depend on a
    ``user_level`` field in one of the quest dicts. If absent, those checks
    are skipped.
    """
    earned: list[dict[str, Any]] = []

    if not completed_quests:
        return earned

    # Number of quests completed
    total_quests = len(completed_quests)

    # Maximum streak found in any completed quest
    max_streak = max((q.get("streak_days", 0) or 0) for q in completed_quests)

    # Count by type (flexible key names)
    type_counts: dict[str, int] = {}
    for q in completed_quests:
        qt = q.get("type") or q.get("quest_type") or ""
        if qt:
            type_counts[qt.lower()] = type_counts.get(qt.lower(), 0) + 1

    daily_count = type_counts.get("daily", 0)
    study_count = type_counts.get("study", 0)

    # Unique goals completed
    goal_ids: set[str] = set()
    for q in completed_quests:
        gid = q.get("goal_id", "")
        if gid:
            goal_ids.add(gid)
    unique_goals = len(goal_ids)

    # S-rank quests
    s_rank_completed = any(
        q.get("rank", "").upper() == "S" for q in completed_quests
    )

    # User level (peek at last quest or best available)
    user_level = 0
    for q in reversed(completed_quests):
        ul = q.get("user_level")
        if ul is not None:
            user_level = int(ul)
            break

    # ── Check each achievement ──────────────────────────────────────────

    # First Steps: 1+ quest
    if total_quests >= 1:
        earned.append(dict(_ACHIEVEMENT_DEFS["first_steps"]))

    # Dedicated: 7+ day streak
    if max_streak >= 7:
        earned.append(dict(_ACHIEVEMENT_DEFS["dedicated"]))

    # Scholar: 10 study quests
    if study_count >= 10:
        earned.append(dict(_ACHIEVEMENT_DEFS["scholar"]))

    # Grinder: 30 daily quests
    if daily_count >= 30:
        earned.append(dict(_ACHIEVEMENT_DEFS["grinder"]))

    # Rising Star: level 5 (if user_level available)
    if user_level >= 5:
        earned.append(dict(_ACHIEVEMENT_DEFS["rising_star"]))

    # Shadow: level 20
    if user_level >= 20:
        earned.append(dict(_ACHIEVEMENT_DEFS["shadow"]))

    # Conqueror: 5 unique goals
    if unique_goals >= 5:
        earned.append(dict(_ACHIEVEMENT_DEFS["conqueror"]))

    # S-Rank Hunter: completed an S-rank quest
    if s_rank_completed:
        earned.append(dict(_ACHIEVEMENT_DEFS["s_rank_hunter"]))

    return earned


# ---------------------------------------------------------------------------
# Convenience: total stats for a profile
# ---------------------------------------------------------------------------


def calculate_total_xp_from_quests(completed_quests: list[dict[str, Any]]) -> int:
    """Return the total XP earned from a list of completed quest dicts.

    Each quest dict is expected to contain a ``"xp_earned"`` key (int).
    If missing, the quest is skipped.
    """
    return sum(q.get("xp_earned", 0) or 0 for q in completed_quests)
