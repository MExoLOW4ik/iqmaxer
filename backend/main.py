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

# Try to import AI course generator
try:
    from ai_course import generate_course, check_answer, generate_hint
    HAS_AI_COURSE = True
except ImportError:
    HAS_AI_COURSE = False

    async def generate_course(_goal: str) -> dict:
        return {"title": "Error", "description": "AI course module not available", "quests": []}

    async def check_answer(_quest: dict, _answer: str) -> dict:
        return {"is_correct": False, "feedback": "AI course module not available"}

    async def generate_hint(_quest: dict) -> str:
        return "AI course module not available"

# Try to import the conversational tutor
try:
    from ai_tutor import open_session as tutor_open, continue_session as tutor_continue
    HAS_AI_TUTOR = True
except ImportError:
    HAS_AI_TUTOR = False

    async def tutor_open(_topic: str) -> str:
        return "The tutor module is not available."

    async def tutor_continue(_history: list) -> str:
        return "The tutor module is not available."


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


class SubmitTestRequest(BaseModel):
    answers: list[int]


class CreateAssignmentRequest(BaseModel):
    title: str
    description: str = ""
    subject: str = ""
    difficulty_rank: str = "E"
    xp_reward: int = 30
    deadline: str = ""


class GenerateCourseRequest(BaseModel):
    goal: str


class AnswerQuestRequest(BaseModel):
    answer: str


class StartTutorRequest(BaseModel):
    topic: str


class TutorMessageRequest(BaseModel):
    content: str


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
# Materials
# ---------------------------------------------------------------------------


@app.get("/api/materials")
async def list_materials():
    return database.get_materials()


@app.get("/api/materials/{material_id}")
async def get_material(material_id: int):
    material = database.get_material(material_id)
    if not material:
        raise HTTPException(status_code=404, detail="Material not found")
    return material


@app.post("/api/materials/{material_id}/study")
async def study_material(material_id: int):
    result = database.study_material(material_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@app.get("/api/tests")
async def list_tests():
    return database.get_tests()


@app.get("/api/tests/{test_id}")
async def get_test(test_id: int):
    test = database.get_test_with_questions(test_id)
    if not test:
        raise HTTPException(status_code=404, detail="Test not found")
    return test


@app.post("/api/tests/{test_id}/submit")
async def submit_test(test_id: int, req: SubmitTestRequest):
    result = database.submit_test(test_id, req.answers)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------


@app.get("/api/assignments")
async def list_assignments():
    return database.get_assignments()


@app.post("/api/assignments")
async def create_assignment(req: CreateAssignmentRequest):
    return database.create_assignment(
        title=req.title,
        description=req.description,
        subject=req.subject,
        difficulty_rank=req.difficulty_rank,
        xp_reward=req.xp_reward,
        deadline=req.deadline,
    )


@app.patch("/api/assignments/{assignment_id}/complete")
async def complete_assignment(assignment_id: int):
    result = database.complete_assignment(assignment_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ---------------------------------------------------------------------------
# AI Course Generator
# ---------------------------------------------------------------------------


@app.post("/api/ai/courses/generate")
async def api_generate_course(req: GenerateCourseRequest):
    """Generate an AI-powered RPG course from a learning goal."""
    if not req.goal.strip():
        raise HTTPException(status_code=400, detail="Goal cannot be empty")

    # Create initial course record
    course = database.create_course(goal=req.goal, title="Generating...", description="")

    try:
        # Call AI to generate the course
        result = await generate_course(req.goal)

        if not result.get("quests") or len(result["quests"]) == 0:
            # Fallback: provide a seed course with the user's goal
            fallback_title = f"Adventure: {req.goal[:50]}"
            database.update_course_status(course["id"], "ready")

            # Single connection for the entire fallback to avoid DB locking
            conn = database._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE ai_courses SET title = ?, description = ? WHERE id = ?",
                (fallback_title, f"AI course generation unavailable. Try again later or explore the seeded courses.", course["id"]),
            )

            # Copy quests from seeded course 1 as fallback (same connection)
            seed_quests = conn.execute(
                'SELECT * FROM ai_quests WHERE course_id = 1 ORDER BY "order" ASC'
            ).fetchall()

            if seed_quests:
                total_xp = 0
                for sq in seed_quests:
                    cursor.execute("""
                        INSERT INTO ai_quests (course_id, title, story, topic, difficulty_rank, xp_reward, correct_answer, hint, "order")
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        course["id"],
                        sq["title"],
                        sq["story"],
                        sq["topic"],
                        sq["difficulty_rank"],
                        sq["xp_reward"],
                        sq["correct_answer"],
                        sq["hint"],
                        sq["order"],
                    ))
                    total_xp += sq["xp_reward"]
                cursor.execute("UPDATE ai_courses SET total_xp = ? WHERE id = ?", (total_xp, course["id"]))
            conn.commit()
            conn.close()
            return database.get_course(course["id"])

        # Update course with generated data
        course_title = result.get("title", "Untitled Course")[:200]
        course_desc = result.get("description", "")[:500]

        # Single connection for the entire AI-generated course save
        # (avoids DB locking from multiple connections)
        conn = database._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE ai_courses SET title = ?, description = ?, status = ? WHERE id = ?",
            (course_title, course_desc, "ready", course["id"]),
        )

        # Add quests and calculate total XP (inline, not via add_quest to stay on same conn)
        total_xp = 0
        for q_data in result["quests"]:
            cursor.execute(
                """INSERT INTO ai_quests
                   (course_id, title, story, topic, difficulty_rank, xp_reward, correct_answer, hint, "order")
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    course["id"],
                    q_data.get("title", ""),
                    q_data.get("story", ""),
                    q_data.get("topic", ""),
                    q_data.get("difficulty_rank", "E"),
                    q_data.get("xp_reward", 20),
                    q_data.get("correct_answer", ""),
                    q_data.get("hint", ""),
                    q_data.get("order", 0),
                ),
            )
            total_xp += q_data.get("xp_reward", 0)

        # Update total XP
        cursor.execute("UPDATE ai_courses SET total_xp = ? WHERE id = ?", (total_xp, course["id"]))
        conn.commit()
        conn.close()

        # Return the full course
        return database.get_course(course["id"])

    except HTTPException:
        raise
    except Exception as e:
        # Close any open connection before updating status (avoids DB lock)
        try:
            conn.close()
        except Exception:
            pass
        database.update_course_status(course["id"], "error", str(e))
        raise HTTPException(status_code=500, detail=f"Course generation failed: {str(e)}")


@app.get("/api/ai/courses")
async def api_list_courses():
    """List all AI-generated courses."""
    return database.get_courses()


@app.get("/api/ai/courses/{course_id}")
async def api_get_course(course_id: int):
    """Get a course with all its quests (correct_answer hidden unless completed)."""
    course = database.get_course(course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


@app.get("/api/ai/courses/{course_id}/stats")
async def api_get_course_stats(course_id: int):
    """Get course completion stats."""
    course = database.get_course(course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    stats = database.get_course_stats(course_id)
    return stats


@app.post("/api/ai/quests/{quest_id}/answer")
async def api_answer_quest(quest_id: int, req: AnswerQuestRequest):
    """Submit an answer to a quest. Checks via AI, awards XP if correct."""
    quest = database.get_quest(quest_id)
    if not quest:
        raise HTTPException(status_code=404, detail="Quest not found")

    if quest["completed"]:
        raise HTTPException(status_code=400, detail="Quest already completed")

    # Check answer via AI
    result = await check_answer(quest, req.answer)
    is_correct = result.get("is_correct", False)
    feedback = result.get("feedback", "")

    # Record attempt and possibly mark completed
    attempt_result = database.complete_quest(quest_id, req.answer, is_correct, feedback)
    if "error" in attempt_result:
        raise HTTPException(status_code=500, detail=attempt_result["error"])

    xp_earned = attempt_result["xp_earned"]
    level_up = False
    fresh_user = None

    # Award XP to user if correct
    if xp_earned > 0:
        xp_result = database.update_user_xp(xp_earned)
        level_up = xp_result.get("level_up", False)
        fresh_user = database.get_user()

    return {
        "correct": is_correct,
        "feedback": feedback,
        "xp_earned": xp_earned,
        "level_up": level_up,
        "user": fresh_user,
    }


@app.post("/api/ai/quests/{quest_id}/hint")
async def api_get_quest_hint(quest_id: int):
    """Generate an AI hint for a quest."""
    quest = database.get_quest(quest_id)
    if not quest:
        raise HTTPException(status_code=404, detail="Quest not found")

    hint = await generate_hint(quest)

    # Save hint to quest for future use
    conn = database._get_conn()
    conn.execute("UPDATE ai_quests SET hint = ? WHERE id = ? AND (hint IS NULL OR hint = '')", (hint, quest_id))
    conn.commit()
    conn.close()

    return {"hint": hint}


# ---------------------------------------------------------------------------
# Conversational Tutor
# ---------------------------------------------------------------------------

# XP awarded each time the learner makes a substantive contribution to a lesson.
TUTOR_TURN_XP = 5
# Mastery gained per learner turn (0..100).
TUTOR_TURN_MASTERY = 8


@app.get("/api/tutor/sessions")
async def api_list_tutor_sessions():
    """List saved tutor sessions for the sidebar."""
    return database.get_tutor_sessions()


@app.post("/api/tutor/sessions")
async def api_start_tutor_session(req: StartTutorRequest):
    """Start a session for a topic: create it and generate the tutor's opening."""
    topic = req.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Topic is required")

    session = database.create_tutor_session(topic)
    opening = await tutor_open(topic)
    database.add_tutor_message(session["id"], "assistant", opening)
    return database.get_tutor_session(session["id"])


@app.get("/api/tutor/sessions/{session_id}")
async def api_get_tutor_session(session_id: int):
    """Get a session with its full message history (resume)."""
    session = database.get_tutor_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.post("/api/tutor/sessions/{session_id}/message")
async def api_tutor_message(session_id: int, req: TutorMessageRequest):
    """Send a learner message; get the tutor's reply plus any XP/mastery update."""
    session = database.get_tutor_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    content = req.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Message is required")

    # Record the learner's message, then ask the tutor for its reply.
    database.add_tutor_message(session_id, "user", content)
    history = database.get_tutor_messages(session_id)
    reply = await tutor_continue(history)
    database.add_tutor_message(session_id, "assistant", reply)

    # Reward engagement: streak + a little XP + topic mastery (heuristic).
    database.update_streak()
    xp_result = database.update_user_xp(TUTOR_TURN_XP)
    mastery = database.bump_tutor_mastery(session_id, TUTOR_TURN_MASTERY)
    fresh_user = database.get_user()

    return {
        "reply": reply,
        "mastery": mastery,
        "xp_earned": TUTOR_TURN_XP,
        "level_up": xp_result.get("level_up", False),
        "user": fresh_user,
    }


@app.delete("/api/tutor/sessions/{session_id}")
async def api_delete_tutor_session(session_id: int):
    """Delete a session and its messages."""
    existed = database.delete_tutor_session(session_id)
    if not existed:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"deleted": True}


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
    uvicorn.run("main:app", host="0.0.0.0", port=8100)
