# iQmaxer Redesign — Friendly Conversational Tutor

**Date:** 2026-06-25
**Status:** Approved design, pending spec review

## Goal

Transform iQmaxer from a Solo Leveling–themed quest grinder into a warm, friendly study app whose **core experience is a conversational tutor** ("a patient professor who slowly routes you toward understanding"), while keeping the existing rank/XP/level/streak progression.

## Constraints & Decisions

1. **Keep gamification.** The rank system (E→D→C→B→A→S), XP, levels, and streaks stay and keep working through the existing `gamification.py` math.
2. **Remove copyrighted IP.** Strip all *Solo Leveling* references: "Hunter", "The System", "Shadow Monarch", "Sung Jinwoo", "arise", and similar. Replace with original, generic framing. Rank letters E–S are generic and may stay.
3. **Tutor is the core.** The conversational tutor becomes the landing experience. Existing features (AI courses/quests, tests, materials, assignments, goal-plans) remain functional but move to secondary navigation.
4. **Friendlier visual design.** Move off the dark edgy RPG look toward a warm, modern, encouraging UI.
5. **Reuse infrastructure.** Reuse the existing LLM provider auto-selection (OpenRouter vs OpenCode Go) and `reasoning_content` fallback. Reuse the single-user `user` row and `gamification.py`.

## Tutor Experience (the heart)

A new multi-turn conversational module. Existing AI modules (`ai_planner.py`, `ai_course.py`) are single-shot request/response; the tutor is genuinely new because it maintains conversation history.

Flow:
- User starts a session by entering a topic (e.g. "derivatives", "photosynthesis").
- The tutor **opens warmly**: a short, friendly introduction to the topic in a professor's voice.
- It then teaches as **concept → example → check**, but each stage is a chat turn, not a fixed wizard:
  - Explains a concept in plain language.
  - Gives a concrete example.
  - Asks the learner a guiding question.
- It **routes based on the learner's reply**: affirms and advances when correct, gently corrects and re-explains when wrong, goes deeper when the learner asks.
- The learner can **interrupt with their own questions at any time**; the tutor answers and returns to the thread.
- Tone: patient, encouraging, never condescending. No game/RPG jargon in tutor speech.

Implementation:
- New module `backend/ai_tutor.py`.
- Each turn: build the message list (system prompt + stored history + new user message), send to the LLM, return the assistant reply.
- System prompt defines the patient-professor persona and the concept→example→check teaching loop, instructing the model to ask one guiding question at a time and adapt to answers.
- Provider/model selection and `reasoning_content` extraction reuse the same pattern as `ai_course.py`.
- **Mastery signal (minimal):** the tutor may optionally emit a lightweight mastery hint that nudges the topic's `mastery` value. To avoid brittle JSON parsing of free-form chat, mastery is advanced by a simple server-side heuristic (e.g. +N per substantive learner turn, capped), not by trusting model-emitted structured data. Keep it simple in v1.

## Data Model (`backend/database.py`)

Two new tables:

```
tutor_sessions:
  id INTEGER PRIMARY KEY
  topic TEXT NOT NULL
  title TEXT              -- short display title (defaults to topic)
  mastery INTEGER DEFAULT 0   -- 0..100, drives per-topic progress
  created_at TEXT
  updated_at TEXT

tutor_messages:
  id INTEGER PRIMARY KEY
  session_id INTEGER NOT NULL  -- FK -> tutor_sessions(id)
  role TEXT NOT NULL           -- 'user' | 'assistant' | 'system'
  content TEXT NOT NULL
  created_at TEXT
```

- New CRUD functions in `database.py`, returning plain dicts/lists like the rest of the module.
- Deleting a session deletes its messages (cascade or explicit delete).
- XP/rank continue to use the existing `user` row + `gamification.py`; advancing a session awards XP via the existing `update_user_xp`.

## API (`backend/main.py`)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/tutor/sessions` | GET | List saved sessions (for sidebar). |
| `/api/tutor/sessions` | POST | Start a session `{topic}` → create session, generate tutor's opening message, return session + opening. |
| `/api/tutor/sessions/{id}` | GET | Session + full message history (resume). |
| `/api/tutor/sessions/{id}/message` | POST | `{content}` → append user message, call LLM with history, return tutor reply + any XP/mastery update. |
| `/api/tutor/sessions/{id}` | DELETE | Delete a session and its messages. |

The tutor module is imported with the same `try/except ImportError` guard pattern used for `ai_planner`/`ai_course`, so the server still boots without it.

## Frontend (`frontend/index.html`)

- **Landing view = Tutor.** ChatGPT-style layout:
  - Left sidebar: list of saved topic sessions + a "New topic" action.
  - Main pane: chat thread — tutor (professor) messages aligned left, learner messages right, with a typing indicator while the tutor responds.
  - Header: friendly progress strip showing level/rank, an XP progress bar, and current streak.
- **Secondary navigation:** existing features (AI courses/quests, tests, materials, assignments, goal-plans) remain reachable but de-emphasized in secondary tabs/nav.
- Single static file, no build step (unchanged).

## Visual Design

- Replace the dark neon RPG theme with a **warm, friendly, modern** look: soft light background (gentle dark option acceptable), rounded cards, generous spacing, readable typography.
- Accent color: warm indigo/teal instead of cold neon blue.
- Encouraging microcopy ("Nice, you've got it!", "3-day streak 🔥", "You've explored 4 topics").
- Rank badges restyled as clean, **original** badges (no Solo Leveling artwork or naming).

## De-branding Checklist

Audit and replace across `backend/ai_planner.py`, `backend/ai_course.py`, `backend/main.py` (fallback plan text), `frontend/index.html`, and `README.md`:
- "Hunter" → "learner" / "you"
- "The System" / "System" persona → generic friendly tutor/app voice
- "Solo Leveling" branding → iQmaxer's own framing
- Any character/lore references (Shadow Monarch, "arise", etc.) → removed
- Keep: rank letters E–S, XP, levels, streaks (generic mechanics).

## Out of Scope (v1)

- Subjects/folders grouping of sessions (sessions are a flat topic list for now).
- Voice, file upload, images in chat.
- Multi-user / auth (app stays single-user).
- Model-driven structured mastery scoring (use a simple server-side heuristic).

## Testing

- Manual: start a session, verify warm opening; send answers (correct/incorrect/question) and verify the tutor routes appropriately; reload and confirm resume; delete a session.
- Verify XP/rank/streak still update.
- Verify de-branding: grep the repo for removed terms returns nothing in shipped strings.
