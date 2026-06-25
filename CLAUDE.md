# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

iQmaxer is a friendly learning app with a light RPG progression layer. Its core surface is a **conversational AI tutor** (`ai_tutor.py`) — a patient professor that teaches a topic through chat (concept → example → guiding question), routing the learner based on their answers. Around it: an AI goal-plan generator (`ai_planner.py`), AI adventure-style STEM courses with numerically-checkable quests (`ai_course.py`), tests, materials, and assignments. Completing things earns XP that raises the player's level and rank (E→D→C→B→A→S).

The app was de-branded from its original *Solo Leveling* theme — the rank/XP/streak mechanics stay, but copyrighted IP ("Hunter", "The System", "Shadow Monarch") was removed in favor of original, friendly framing. Keep it that way.

## Running

```bash
# Backend (serves API + static frontend at http://0.0.0.0:8100)
cd backend
python3 main.py                # or: python3 -m uvicorn main:app --host 0.0.0.0 --port 8100
```

There is **no `requirements.txt`** despite the README referencing one. Dependencies are `fastapi`, `uvicorn`, `httpx`, `pydantic` — install manually. There are **no tests** and no lint config.

`backend/start.sh` is the production launcher (runs from `/var/www/iqmaxer/backend`). Note it does `rm -f iqmaxer.db*` on every start — **it wipes the SQLite database each launch**. Do not use it for local development if you want to keep data.

The frontend is a single static file (`frontend/index.html`) mounted by FastAPI — no build step.

## AI provider configuration

Both `ai_planner.py` (goal plans) and `ai_course.py` (STEM courses) use the same OpenAI-compatible HTTP pattern and auto-select a provider from env vars:

- If `OPENROUTER_API_KEY` is set → OpenRouter (`openai/gpt-4o`, fallback `anthropic/claude-sonnet-4`).
- Else → OpenCode Go via `OPENCODE_GO_API_KEY` / `OPENCODE_GO_BASE_URL` (default `https://opencode.ai/zen/go/v1`), models `glm-5.2` / `deepseek-v4-pro`.
- If no key is set, the planner falls back to `generate_fallback_plan` in `main.py`; the course generator returns an error course.

GLM on OpenCode Go sometimes returns the answer in `reasoning_content` instead of `content` — both modules have fallback logic to extract it (and `ai_course.py` regex-extracts JSON buried in reasoning text). LLM responses are parsed defensively: markdown code fences are stripped and JSON is validated/normalized before use.

## Architecture

Backend is a flat 5-module FastAPI app in `backend/`; all cross-module imports are siblings (`main.py` inserts its own dir on `sys.path`). AI modules are imported in `try/except ImportError` blocks so the server still boots if they're absent.

- **`main.py`** — FastAPI app, request models, all HTTP endpoints, and the no-AI `generate_fallback_plan`. CORS is wide open. DB is initialized on startup.
- **`database.py`** — all SQLite access. Every function returns plain dicts/lists (no cursor leakage) via `_get_conn()`, which sets `busy_timeout=5000`, WAL mode, and foreign keys on. `DB_PATH` is `backend/iqmaxer.db`. This is the single source of persistence; there is no ORM.
- **`gamification.py`** — pure functions, no I/O. Owns the XP/level/rank math: `get_level`/`get_rank` (cumulative-XP quadratic curve, `_XP_PER_LEVEL_FACTOR=100`), `quest_xp_reward` by rank, `calculate_streak_bonus` (×1.0→×5.0), `check_achievements`, and level-up benefits. When changing progression rules, change them here.
- **`ai_tutor.py`** — the conversational tutor. Unlike the other AI modules (single-shot), it keeps a multi-turn message history: each turn sends `system prompt + stored history + new message` to the LLM. Sessions/messages persist in `tutor_sessions` + `tutor_messages`; the `/api/tutor/...` endpoints drive it. Mastery advances via a simple server-side per-turn heuristic (`TUTOR_TURN_MASTERY` in `main.py`), not model-emitted JSON.
- **`ai_planner.py`** — goal → structured quest plan (the original feature).
- **`ai_course.py`** — goal → Solo Leveling STEM course with quests that have a `correct_answer`; also `check_answer` (AI grading with ±5% numeric tolerance, plus a non-AI exact/numeric fallback) and `generate_hint`.

### Two parallel quest systems (important)

There are **two distinct quest concepts** backed by separate tables, and `database.py` defines two functions named `complete_quest` (the later definition for AI quests shadows the earlier one — they are not interchangeable):

1. **Goal quests** — `goals` + `quests` tables. Daily/monthly/yearly tasks from `ai_planner`. Endpoints under `/api/goals`, `/api/quests`, `/api/daily`.
2. **AI course quests** — `ai_courses` + `ai_quests` + `ai_attempts` tables. STEM quests with answer-checking from `ai_course`. Endpoints under `/api/ai/courses` and `/api/ai/quests`.

Additional standalone features have their own tables and endpoints: `materials` (`/api/materials`), `tests` + `test_questions` + `test_results` (`/api/tests`, with server-side answer shuffling via `_shuffle_question`), and `assignments` (`/api/assignments`).

## Accounts & auth

The app is **multi-user**. `backend/auth.py` handles PBKDF2 password hashing (stdlib, no deps) and opaque session tokens stored in the `sessions` table; the frontend keeps the token in `localStorage` and sends `Authorization: Bearer <token>`. The FastAPI dependency `auth.current_user` resolves the token to a user (401 otherwise) and gates every personal endpoint — pass `user["id"]` into the database calls.

- **Per-user** (rows carry `user_id`): `users` (profile + XP/level/rank/streak), `tutor_sessions`, `goals`/`quests`, `assignments`, `achievements`, `test_results`, `ai_courses`. All their DB functions take `user_id` and filter/own-check by it.
- **Shared/global**: the `materials` and `tests`/`test_questions` content libraries (currently unseeded/empty).
- `database.get_user(user_id)`, `update_user_xp(user_id, xp)`, and `update_streak(user_id)` all require the user id — there is no implicit "user 1" anymore.
- The whole SPA is **gated by a cosmic welcome screen**: `init()` checks the token via `GET /api/auth/me`; failure shows the login/register screen instead of the app.
- There is **no DB migration** path — the schema is treated as a fresh build (consistent with `start.sh` wiping the DB on boot).
