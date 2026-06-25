# iQmaxer — Accounts, Welcome Screen, Cosmic AI & Harder Themes

**Date:** 2026-06-25
**Status:** Approved design, pending spec review

## Goal

Add real multi-user accounts with a gated cosmic welcome screen, give the AI a
distinctive "quantum-lab" visual identity, and skew the learning content toward
much harder subjects (advanced math, quantum physics). Builds on the
conversational tutor redesign.

## Constraints & Decisions

1. **Real accounts**, username + password. Passwords hashed with **PBKDF2 via Python's stdlib `hashlib`** — no new dependencies.
2. **Token auth**: login returns a random opaque session token stored in a `sessions` table; the frontend keeps it in `localStorage` and sends `Authorization: Bearer <token>`. A FastAPI dependency resolves the token to the current user (401 if missing/invalid).
3. **Data scope**: personal progress is per-user; the seeded content library is shared.
   - Per-user (add `user_id`): `tutor_sessions`, `goals`, `quests`, `assignments`, `achievements`, `test_results`, `ai_courses`. XP/level/rank/streak live on the `users` row.
   - Shared/global (unchanged): `materials`, `tests`, `test_questions`.
4. **Fresh schema, no migration.** `start.sh` already wipes the DB on boot and there is no real production data, so the schema change is treated as a clean rebuild. (If real data must be preserved, this decision changes.)
5. **Cosmic / quantum-lab** visual identity, layered additively over the existing warm theme — strongest on the welcome screen and tutor.
6. **Harder themes**: advanced default topic suggestions plus an "Advanced mode" that raises the tutor's rigor.

## Authentication (`backend/auth.py`, new)

A small, focused module — no framework coupling beyond a FastAPI dependency:

- `hash_password(password) -> "pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>"` using `hashlib.pbkdf2_hmac` (≥200k iterations), random 16-byte salt from `secrets`.
- `verify_password(password, stored) -> bool` — constant-time compare via `hmac.compare_digest`.
- `new_token() -> str` — `secrets.token_urlsafe(32)`.
- DB helpers live in `database.py`: `create_user`, `get_user_by_username`, `create_session(token, user_id)`, `get_user_by_token(token)`, `delete_session(token)`.
- FastAPI dependency `current_user(authorization: str = Header(None))` — extracts the bearer token, resolves it, raises `HTTPException(401)` if invalid. Returns the user dict.

### Auth endpoints (`main.py`)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/auth/register` | POST | `{username, password, display_name?, avatar?}` → create user, return `{token, user}`. 409 if username taken. Basic validation (username/password length). |
| `/api/auth/login` | POST | `{username, password}` → verify, create session, return `{token, user}`. 401 on bad creds. |
| `/api/auth/logout` | POST | Delete the current session token. Requires auth. |
| `/api/auth/me` | GET | Return the current user (validates token). |

## Data Model Changes (`backend/database.py`)

New tables:

```
users:
  id INTEGER PRIMARY KEY
  username TEXT UNIQUE NOT NULL
  password_hash TEXT NOT NULL          -- full pbkdf2 string (salt embedded)
  display_name TEXT
  avatar TEXT                          -- emoji or short id
  level INTEGER DEFAULT 1
  xp INTEGER DEFAULT 0
  rank TEXT DEFAULT 'E'
  streak INTEGER DEFAULT 0
  last_active_date TEXT
  created_at TEXT

sessions:
  token TEXT PRIMARY KEY
  user_id INTEGER NOT NULL             -- FK -> users(id)
  created_at TEXT
```

- The legacy single-row `user` table and its `get_user()`/`update_user_xp()`/`update_streak()` helpers are reworked to operate on a `user_id`:
  - `get_user()` → `get_user(user_id)`
  - `update_user_xp(added_xp)` → `update_user_xp(user_id, added_xp)`
  - `update_streak()` → `update_streak(user_id)`
- Add `user_id` column + scoping to: `tutor_sessions`, `goals`, `quests` (via their goal), `assignments`, `achievements`, `test_results`, `ai_courses`. All their CRUD functions take `user_id` and filter by it.
- `materials`, `tests`, `test_questions` stay global (seed once, shared).
- `seed_ai_courses()` is removed or made global-template-free; generated courses are now per-user, so no shared seeded AI courses. Seeded `materials`/`tests` remain.

### Endpoint wiring

Every personal endpoint gains `user = Depends(current_user)` and passes `user["id"]` into the database calls. Tutor, goals, quests, assignments, achievements, test submission/results, and AI course endpoints are all protected. `materials` and `tests` listing stay readable but still require a logged-in user (the whole app is gated).

## Welcome / Auth Screen (frontend)

- A full-screen cosmic landing shown whenever no valid token is present; it gates the entire SPA. On load, `init()` checks `localStorage` token via `GET /api/auth/me`; on failure it shows the welcome screen instead of the app.
- Visuals: a `<canvas>` starfield with slowly drifting glowing particles, a few faint floating equations (`∂ψ/∂t = Ĥψ`, `∮ E·dl`, `eⁱᵖ+1=0`), and a central frosted panel.
- Panel: iQmaxer mark + glowing "thinking orb", a one-line tagline, and a Login / Register toggle. Register adds display name + an avatar (emoji) picker. Errors shown inline.
- On success: store token, fetch user, animate into the app.
- Logout button in the HUD clears the token and returns to the welcome screen.

## Cosmic / Quantum-Lab AI Look (frontend)

- A theme layer added over the existing warm palette: deep space-violet background, a lightweight starfield/particle canvas behind content, faint formula-texture motifs.
- **Thinking orb**: a pure-CSS pulsing/rotating gradient sphere replaces the tutor's typing dots while the AI responds; assistant messages get an orb avatar and a subtle glow.
- HUD level/rank badges restyled as glowing "energy" chips (E–S ranks unchanged).
- Performance: the canvas uses a capped particle count and `requestAnimationFrame`, pausing when the tab is hidden, so it stays light on mobile.

## Harder Themes (frontend + AI)

- Tutor welcome suggestions become advanced by default: Quantum Mechanics, Real Analysis, Tensor Calculus, Quantum Field Theory, Abstract Algebra, General Relativity, Stochastic Calculus, Statistical Mechanics.
- **Advanced mode toggle** on the tutor. When enabled, the message request includes an `advanced: true` flag; the backend selects an advanced variant of the tutor system prompt that assumes university/graduate level, uses rigorous notation, and goes deep — while still routing the learner step by step rather than dumping full solutions.
- `ai_course.py` `generate_course` gains an optional difficulty hint so generated quests can target the harder A/S ranks.

## Error Handling

- All protected endpoints return 401 with a clear detail when the token is missing/invalid; the frontend treats any 401 as "session expired" → clears the token and shows the welcome screen.
- Register validates username uniqueness (409) and minimum username/password lengths (400).
- Auth failures never reveal whether the username or the password was wrong (generic "Invalid credentials").

## Testing

- Register → returns token + user; duplicate username → 409.
- Login with right/wrong password → token / 401.
- Protected endpoint without token → 401; with token → 200.
- Two users: each sees only their own tutor sessions, goals, XP; shared materials/tests visible to both.
- `/api/auth/me` with valid/invalid token; logout invalidates the token.
- Frontend: welcome screen gates the app; successful login reveals it; logout returns to welcome; advanced-mode flag changes the prompt path; JS parses; cosmic theme + thinking orb render.

## Out of Scope (v1)

- Password reset / email verification / OAuth.
- Per-user copies of materials and tests.
- Token expiry/refresh (tokens are long-lived until logout).
- Real-time multiplayer or social features.
