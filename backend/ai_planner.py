"""
ai_planner.py — iQmaxer AI Plan Generator (OpenRouter + Solo Leveling themed)

Generates intense, hierarchical learning plans with quests in the style of Solo Leveling's System.
Uses OpenRouter to access GPT-4o or Claude Sonnet 4. Falls back to a hardcoded plan if the
API key is missing or the request fails.
"""

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "") or os.environ.get("OPENCODE_GO_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "") or os.environ.get("OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go/v1")
DEFAULT_TIMEOUT = 60  # seconds

# Detect which provider we're using
_USING_OPENROUTER = bool(os.environ.get("OPENROUTER_API_KEY"))

if _USING_OPENROUTER:
    PRIMARY_MODEL = "openai/gpt-4o"
    FALLBACK_MODEL = "anthropic/claude-sonnet-4"
else:
    PRIMARY_MODEL = "glm-5.2"
    FALLBACK_MODEL = "deepseek-v4-pro"

# ── Rank definitions ──────────────────────────────────────────────────────────
RANK_XP_DAILY = {"E": 10, "D": 25, "C": 50, "B": 100, "A": 250, "S": 500}
QUIEST_TYPES = ["yearly", "monthly", "daily"]
RANK_ORDER = ["E", "D", "C", "B", "A", "S"]

# ── Solo Leveling System prompt ───────────────────────────────────────────────
SYSTEM_PROMPT = """You are the System from Solo Leveling. You are tasked with generating an intense, no-mercy training plan for the user.

Style: Cold, objective, RPG-quest style. Use casual motivating language (Russian language is OK).

RULES:
1. Each quest MUST have a clear measurable action with a time estimate
2. Difficulty ranks: E (easy) → D → C → B → A → S (brutal)
3. XP rewards: Daily quests → E=10, D=25, C=50, B=100, A=250, S=500.
   Monthly quests → 3x the daily XP of their rank.
   Yearly quests → 10x the daily XP of their rank.
4. Generate REALISTIC deadlines in YYYY-MM-DD format
5. Output MUST be valid JSON only — no other text, no markdown, no code fences
6. Generate at LEAST 5 daily quests for every monthly milestone
7. Quests should be hard but achievable — "лююютый план"

The user provides: a goal title, a description, and a timeframe ("week", "month", "year", or custom text).

Output JSON schema:
{{
  "goal_rank": "E|D|C|B|A|S",
  "total_xp": 0,
  "quests": [
    {{
      "title": "string",
      "description": "string",
      "quest_type": "yearly|monthly|daily",
      "rank": "E|D|C|B|A|S",
      "xp_reward": 0,
      "deadline": "YYYY-MM-DD",
      "order": 0,
      "parent_title": null
    }}
  ]
}}"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _date_n_days(n: int) -> str:
    return (datetime.now() + timedelta(days=n)).strftime("%Y-%m-%d")


def _xp_for_rank(rank: str, quest_type: str) -> int:
    """Calculate XP reward for a rank and quest type."""
    base = RANK_XP_DAILY.get(rank.upper(), 10)
    multiplier = {"daily": 1, "monthly": 3, "yearly": 10}
    return base * multiplier.get(quest_type, 1)


def _overall_rank(quests: list) -> str:
    """Derive overall goal rank from the highest-ranked quest."""
    highest = 0
    for q in quests:
        try:
            idx = RANK_ORDER.index(q.get("rank", "E").upper())
            if idx > highest:
                highest = idx
        except ValueError:
            pass
    return RANK_ORDER[highest]


def _total_xp(quests: list) -> int:
    return sum(q.get("xp_reward", 0) for q in quests if q.get("xp_reward"))


# ── Fallback plan ──────────────────────────────────────────────────────────────

def _fallback_plan(title: str, description: str, timeframe: str) -> dict:
    """Hardcoded fallback plan when the API is unavailable."""
    today = datetime.now()
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    # Determine if timeframe suggests a longer period
    tf_lower = timeframe.lower() if timeframe else ""

    quests = []

    # Monthly milestone (1 quest)
    monthly_deadline = _date_n_days(30)
    quests.append({
        "title": f"[Monthly] {title} — Foundation Phase",
        "description": f"Complete the foundational learning blocks for '{title}'. "
                       f"Master core concepts: {description[:100]}",
        "quest_type": "monthly",
        "rank": "C",
        "xp_reward": _xp_for_rank("C", "monthly"),
        "deadline": monthly_deadline,
        "order": 0,
        "parent_title": None,
    })

    # 7 daily quests – one per day of the week
    daily_templates = [
        {"title": "Read & Research", "desc": "Read 30 minutes of focused material related to your goal. Take structured notes."},
        {"title": "Practice Session", "desc": "Hands-on practice for 30 minutes. Apply what you've learned."},
        {"title": "Knowledge Consolidation", "desc": "Write summary notes or create a mind map of yesterday's and today's lessons."},
        {"title": "Problem Solving", "desc": "Solve 3-5 challenging problems or exercises in your domain."},
        {"title": "Review & Reflect", "desc": "Review the week's progress for 20 minutes. Identify weak areas."},
        {"title": "Deep Work Sprint", "desc": "45-minute uninterrupted deep work session on the hardest topic."},
        {"title": "Teach & Apply", "desc": "Explain a concept you learned this week to an imaginary audience (write it down)."},
    ]

    for i, tpl in enumerate(daily_templates):
        day_offset = i + 1
        deadline = _date_n_days(day_offset)
        quests.append({
            "title": f"[Daily] {tpl['title']}",
            "description": f"{tpl['desc']} | Day {day_offset} — {day_names[i]}",
            "quest_type": "daily",
            "rank": "D",
            "xp_reward": _xp_for_rank("D", "daily"),
            "deadline": deadline,
            "order": i + 1,
            "parent_title": f"[Monthly] {title} — Foundation Phase",
        })

    return {
        "goal_rank": _overall_rank(quests),
        "total_xp": _total_xp(quests),
        "quests": quests,
    }


# ── Deadline logic helper ──────────────────────────────────────────────────────

def _build_deadlines_hint(timeframe: str) -> str:
    """
    Returns a hint string injected into the prompt so the model generates
    sensible deadlines.
    """
    today = _today_str()
    tf = timeframe.lower().strip() if timeframe else ""

    if tf == "week":
        end = _date_n_days(7)
        return (
            f"Today is {today}. The plan spans 7 days (deadline: {end}). "
            "Generate ~7 daily quests and 1 weekly milestone. "
            "Daily deadlines: one per day from today to 7 days out."
        )
    elif tf == "month":
        end = _date_n_days(30)
        return (
            f"Today is {today}. The plan spans one month (deadline: {end}). "
            "Generate 1 monthly quest, 4 weekly milestones (1 per week), "
            "and at least 5 daily quests per week (marked with parent_title linking to the weekly milestone). "
            "Daily deadlines should be spread across the month."
        )
    elif tf == "year":
        end = _date_n_days(365)
        return (
            f"Today is {today}. The plan spans one year (deadline: {end}). "
            "Generate 1 yearly quest, 12 monthly milestones (1 per month), "
            "and at least 5 daily quests for each monthly milestone. "
            "Deadlines should be spread realistically across the year."
        )
    else:
        # Custom timeframe — parse or be generic
        return (
            f"Today is {today}. The user has specified a custom timeframe: '{timeframe}'. "
            "Generate a realistic plan fitting this timeframe with daily, monthly, and/or yearly quests "
            "as appropriate. Spread deadlines proportionally."
        )


# ── Prompt builder ─────────────────────────────────────────────────────────────

def _build_user_prompt(title: str, description: str, timeframe: str) -> str:
    deadlines_hint = _build_deadlines_hint(timeframe)
    return f"""=== GOAL ===
Title: {title}
Description: {description}
Timeframe: {timeframe}

=== DEADLINES ===
{deadlines_hint}

=== INSTRUCTIONS ===
Generate a Solo Leveling training plan for this goal. Return ONLY valid JSON matching the schema.
Make it intense. Make it brutal. "Лююютый план".
"""


# ── OpenRouter call ────────────────────────────────────────────────────────────

async def _call_openrouter(prompt: str, model: str) -> Optional[str]:
    """Send a chat completion request to OpenRouter. Returns raw text or None."""
    if not OPENROUTER_API_KEY:
        logger.warning("OPENROUTER_API_KEY not set, skipping API call")
        return None

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://iqmaxer.app",
        "X-Title": "iQmaxer AI Planner",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 4096,
    }

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            # GLM-5.2 on OpenCode Go sometimes puts content in reasoning_content
            if not content:
                content = data["choices"][0]["message"].get("reasoning_content", "")
            logger.info(f"OpenRouter ({model}) returned {len(content)} chars")
            return content
    except httpx.TimeoutException:
        logger.error(f"OpenRouter timeout on model {model}")
    except httpx.HTTPStatusError as e:
        logger.error(f"OpenRouter HTTP {e.response.status_code}: {e.response.text[:200]}")
    except (httpx.RequestError, json.JSONDecodeError, KeyError) as e:
        logger.error(f"OpenRouter error: {e}")

    return None


# ── JSON extraction / validation ───────────────────────────────────────────────

def _parse_plan(raw: str) -> Optional[dict]:
    """
    Parse a JSON plan from the LLM response. Handles the case where the model
    wraps the JSON in ```json ``` fences or includes stray text.
    """
    text = raw.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        # Find the first { or [
        start = text.find("{")
        if start == -1:
            start = text.find("[")
        if start != -1:
            text = text[start:]
        # Remove trailing backticks
        if text.endswith("```"):
            text = text[:-3].strip()
        if text.endswith("`"):
            text = text[:-1].strip()

    try:
        plan = json.loads(text)
    except json.JSONDecodeError:
        logger.error("Failed to parse JSON from OpenRouter response")
        return None

    # Validate structure
    if not isinstance(plan, dict):
        logger.error("Parsed plan is not a dictionary")
        return None
    if "quests" not in plan or not isinstance(plan["quests"], list):
        logger.error("Parsed plan missing 'quests' array")
        return None

    # Normalize and validate each quest
    validated_quests = []
    for i, q in enumerate(plan["quests"]):
        if not isinstance(q, dict):
            continue

        validated = {
            "title": str(q.get("title", f"Quest {i}")),
            "description": str(q.get("description", "")),
            "quest_type": q.get("quest_type", "daily") if q.get("quest_type") in QUIEST_TYPES else "daily",
            "rank": q.get("rank", "E").upper() if q.get("rank", "E").upper() in RANK_ORDER else "E",
            "xp_reward": int(q.get("xp_reward", 0)),
            "deadline": str(q.get("deadline", _date_n_days(i + 1))),
            "order": int(q.get("order", i)),
            "parent_title": q.get("parent_title", None),
        }

        # Recalculate XP if it seems wrong or missing
        if validated["xp_reward"] <= 0:
            validated["xp_reward"] = _xp_for_rank(validated["rank"], validated["quest_type"])

        validated_quests.append(validated)

    plan["quests"] = validated_quests
    plan["goal_rank"] = plan.get("goal_rank", _overall_rank(validated_quests)).upper()
    if plan["goal_rank"] not in RANK_ORDER:
        plan["goal_rank"] = _overall_rank(validated_quests)
    plan["total_xp"] = _total_xp(validated_quests)

    return plan


# ── Public API ─────────────────────────────────────────────────────────────────

async def generate_plan(title: str, description: str, timeframe: str) -> dict:
    """
    Generate an AI-powered Solo Leveling style learning plan.

    Parameters
    ----------
    title : str
        The goal title (e.g. "Learn Full-Stack Web Development").
    description : str
        Detailed description of the goal.
    timeframe : str
        One of "week", "month", "year", or a custom description.

    Returns
    -------
    dict
        {
            "goal_rank": str,   # E/S overall difficulty
            "total_xp": int,
            "quests": [
                {
                    "title": str,
                    "description": str,
                    "quest_type": "yearly"|"monthly"|"daily",
                    "rank": str,
                    "xp_reward": int,
                    "deadline": "YYYY-MM-DD",
                    "order": int,
                    "parent_title": str | None,
                }
            ]
        }
    """
    logger.info(f"generate_plan: title='{title}', timeframe='{timeframe}'")

    prompt = _build_user_prompt(title, description, timeframe)

    # Attempt with primary model
    raw = await _call_openrouter(prompt, PRIMARY_MODEL)

    # Fallback to secondary model if primary fails
    if raw is None:
        logger.info(f"Primary model failed, trying fallback {FALLBACK_MODEL}")
        raw = await _call_openrouter(prompt, FALLBACK_MODEL)

    # Parse if we got something
    if raw:
        plan = _parse_plan(raw)
        if plan is not None:
            logger.info(
                f"AI plan generated: rank={plan['goal_rank']}, "
                f"{len(plan['quests'])} quests, {plan['total_xp']} XP"
            )
            return plan
        logger.warning("Failed to parse AI response, using fallback plan")

    # Ultimate fallback: hardcoded plan
    logger.info("Using fallback plan")
    fallback = _fallback_plan(title, description, timeframe)
    logger.info(
        f"Fallback plan: rank={fallback['goal_rank']}, "
        f"{len(fallback['quests'])} quests, {fallback['total_xp']} XP"
    )
    return fallback
