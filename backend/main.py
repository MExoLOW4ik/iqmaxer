"""
main.py — iQmaxer FastAPI Server

Serves at http://0.0.0.0:8100
Mounts static frontend from ../frontend/
"""

import os
import sys

# Ensure we can import sibling modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database

# Try to import AI planner (may not exist yet)
try:
    from ai_planner import generate_plan
    HAS_AI = True
except ImportError:
    HAS_AI = False

    async def generate_plan(_title: str, _desc: str, _timeframe: str) -> dict:
        return generate_fallback_plan(_title, _desc, _timeframe)


from gamification import (
    calculate_streak_bonus,
    check_achievements,
)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="iQmaxer", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup() -> None:
    database.init_db()
    user = database.get_user()
    if not user:
        database.init_db()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateGoalRequest(BaseModel):
    title: str
    description: str = ""
    timeframe: str = "month"
    target_date: str = ""


# ---------------------------------------------------------------------------
# Fallback plan generator (used when ai_planner is missing)
# ---------------------------------------------------------------------------

def generate_fallback_plan(title: str, desc: str, timeframe: str) -> dict:
    """Generate a simple plan with 3–5 daily quests + 1 milestone."""
    quests = [
        {
            "title": f"Research & outline: {title}",
            "description": f"Spend 30 min researching {title} and create an outline.",
            "quest_type": "daily",
            "rank": "E",
            "xp_reward": 10,
            "order": 0,
        },
        {
            "title": f"Deep work session on {title}",
            "description": f"Focused 1-hour session working on {desc or title}.",
            "quest_type": "daily",
            "rank": "E",
            "xp_reward": 15,
            "order": 1,
        },
        {
            "title": f"Review & refine {title}",
            "description": "Review what you've done so far and plan next steps.",
            "quest_type": "daily",
            "rank": "E",
            "xp_reward": 10,
            "order": 2,
        },
    ]

    if timeframe in ("month", "year"):
        quests.append(
            {
                "title": f"Monthly milestone: {title}",
                "description": f"Achieve a key milestone for {title}.",
                "quest_type": "monthly",
                "rank": "D",
                "xp_reward": 50,
                "order": 3,
            }
        )

    if timeframe == "year":
        quests.append(
            {
                "title": f"Quarterly checkpoint: {title}",
                "description": "Major progress review and goal adjustment.",
                "quest_type": "monthly",
                "rank": "C",
                "xp_reward": 100,
                "order": 4,
            }
        )

    return {"quests": quests}


# ---------------------------------------------------------------------------
# Helper: award new achievements and optionally bonus XP
# ---------------------------------------------------------------------------

def _award_new_achievements(completed_quests: list[dict], user: dict) -> list[dict]:
    """Evaluate achievements, persist new ones, and return them.

    Also applies XP bonus from newly unlocked achievements to the user.
    """
    now_unlocked = check_achievements(completed_quests)
    new_ones = []
    for ach in now_unlocked:
        saved = database.add_achievement(ach["id"], ach["name"])
        if saved:
            # Grant XP bonus for this achievement
            xp_bonus = ach.get("xp_bonus", 0)
            if xp_bonus:
                database.update_user_xp(xp_bonus)
            new_ones.append(ach)
    return new_ones


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


@app.get("/api/user")
async def get_user():
    user = database.get_user()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.get("/api/goals")
async def list_goals():
    return database.get_goals()


@app.get("/api/goals/{goal_id}")
async def get_goal(goal_id: int):
    goal = database.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    quests = database.get_quests(goal_id)
    return {**goal, "quests": quests}


@app.post("/api/goals")
async def create_goal(req: CreateGoalRequest):
    plan = await generate_plan(req.title, req.description, req.timeframe)
    goal = database.create_goal(
        title=req.title,
        description=req.description,
        timeframe=req.timeframe,
        target_date=req.target_date,
        generated_plan=plan,
    )
    return goal


@app.patch("/api/quests/{quest_id}")
async def complete_quest_endpoint(quest_id: int):
    # --- 1. Mark quest completed in DB ---
    result = database.complete_quest(quest_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])

    quest = result["quest"]
    base_xp = result["xp_reward"]

    # --- 2. Apply streak bonus ---
    user = database.get_user()
    streak = user.get("streak", 0)
    multiplier = calculate_streak_bonus(streak)
    total_xp = int(base_xp * multiplier)

    # --- 3. Update streak (this will also increment if consecutive) ---
    database.update_streak()

    # --- 4. Award XP and check level-up ---
    xp_result = database.update_user_xp(total_xp)

    # --- 5. Check achievements ---
    completed_quests = database.get_history()
    new_achievements = _award_new_achievements(completed_quests, user)

    # Re-fetch fresh user stats
    fresh_user = database.get_user()

    return {
        "quest": quest,
        "xp_earned": total_xp,
        "base_xp": base_xp,
        "streak_multiplier": multiplier,
        "streak": fresh_user.get("streak", 0),
        "level_up": xp_result["level_up"],
        "new_level": xp_result["level"],
        "new_xp": xp_result["xp"],
        "new_rank": xp_result["rank"],
        "benefits": xp_result.get("benefits", {}),
        "new_achievements": new_achievements,
    }


@app.get("/api/daily")
async def get_daily():
    return database.get_daily_quests()


@app.get("/api/history")
async def get_history():
    return database.get_history()


@app.get("/api/achievements")
async def list_achievements():
    return database.get_achievements()


# ---------------------------------------------------------------------------
# Static frontend (must be last — catch-all)
# ---------------------------------------------------------------------------

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8100, reload=True)
