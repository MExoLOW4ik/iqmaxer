"""
ai_tutor.py — iQmaxer Conversational Tutor

A multi-turn AI tutor that teaches a topic like a patient professor:
opens warmly, explains a concept, gives an example, asks a guiding question,
and routes the learner based on their answers. Unlike ai_planner / ai_course
(single-shot), this module keeps a full conversation history and replies turn
by turn.

Reuses the same OpenAI-compatible provider auto-selection and reasoning_content
fallback as the rest of the backend.
"""

import os
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "") or os.environ.get("OPENCODE_GO_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "") or os.environ.get("OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go/v1")
DEFAULT_TIMEOUT = 120  # seconds

_USING_OPENROUTER = bool(os.environ.get("OPENROUTER_API_KEY"))

if _USING_OPENROUTER:
    PRIMARY_MODEL = "openai/gpt-4o"
    FALLBACK_MODEL = "anthropic/claude-sonnet-4"
else:
    PRIMARY_MODEL = "glm-5.2"
    FALLBACK_MODEL = "deepseek-v4-pro"


# ── The patient-professor persona ───────────────────────────────────────────

TUTOR_SYSTEM_PROMPT = """You are a warm, patient tutor in the iQmaxer learning app — think of a friendly professor sitting beside the learner, guiding them one step at a time.

Your teaching style:
1. Open warmly. When a topic begins, greet the learner kindly and briefly say what you'll explore together — make them feel welcome, never intimidated.
2. Teach in a gentle loop: explain ONE small concept in plain language, give a concrete example, then ask ONE short guiding question to check understanding.
3. Wait for the learner's reply, then ROUTE based on it:
   - If they're right: affirm warmly, then build on it with the next small step.
   - If they're partly right or wrong: never make them feel bad. Gently point them in the right direction with a hint, then ask again or re-explain more simply.
   - If they ask their own question: answer it clearly, then return to the thread.
4. Keep replies short and conversational — a few sentences, not a wall of text. One idea at a time.
5. Use simple language, encouraging tone, and the occasional friendly emoji where it feels natural (don't overdo it).
6. Never dump the full solution at once. Lead the learner to discover it.

Do NOT use any role-play game/RPG framing, ranks, "hunter", "quest", or fantasy lore in your teaching. You are simply a kind, real tutor. Respond in the same language the learner writes in."""


# ── LLM call ─────────────────────────────────────────────────────────────────

async def _call(messages: list[dict], model: str) -> Optional[str]:
    """Send a chat completion with full message history. Returns text or None."""
    if not OPENROUTER_API_KEY:
        logger.warning("No API key set, skipping tutor LLM call")
        return None

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://iqmaxer.app",
        "X-Title": "iQmaxer Tutor",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1200,
    }

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            logger.info(f"Tutor calling {model} ({len(messages)} msgs)")
            response = await client.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            msg = data["choices"][0]["message"]
            content = msg.get("content")
            # GLM on OpenCode Go sometimes puts the answer in reasoning_content
            if not content or not content.strip():
                content = msg.get("reasoning_content", "")
            return content.strip() if content else None
    except httpx.TimeoutException:
        logger.error(f"Tutor timeout on model {model}")
    except httpx.HTTPStatusError as e:
        logger.error(f"Tutor HTTP {e.response.status_code}: {e.response.text[:200]}")
    except (httpx.RequestError, KeyError, ValueError) as e:
        logger.error(f"Tutor error: {e}")
    return None


def _build_messages(history: list[dict]) -> list[dict]:
    """Prepend the system prompt to the stored conversation history.

    `history` items are dicts with 'role' and 'content'. Stored system rows are
    ignored — we always use the canonical TUTOR_SYSTEM_PROMPT.
    """
    messages = [{"role": "system", "content": TUTOR_SYSTEM_PROMPT}]
    for m in history:
        role = m.get("role")
        if role in ("user", "assistant"):
            messages.append({"role": role, "content": m.get("content", "")})
    return messages


# ── Public API ───────────────────────────────────────────────────────────────

async def open_session(topic: str) -> str:
    """Generate the tutor's warm opening message for a new topic."""
    history = [{
        "role": "user",
        "content": f"I'd like to learn about: {topic}. Please start teaching me.",
    }]
    return await _reply(history, fallback=_fallback_opening(topic))


async def continue_session(history: list[dict]) -> str:
    """Generate the tutor's next reply given the full conversation history."""
    return await _reply(history, fallback=_fallback_reply())


async def _reply(history: list[dict], fallback: str) -> str:
    messages = _build_messages(history)
    raw = await _call(messages, PRIMARY_MODEL)
    if raw is None:
        raw = await _call(messages, FALLBACK_MODEL)
    return raw if raw else fallback


def _fallback_opening(topic: str) -> str:
    return (
        f"Hi! 👋 I'm glad you want to learn about **{topic}**. "
        "(The AI tutor isn't reachable right now, so I can't teach interactively — "
        "please check the server's API key configuration.) "
        "Once it's connected, I'll walk you through it step by step."
    )


def _fallback_reply() -> str:
    return (
        "Sorry — I couldn't reach the tutor service just now. "
        "Please try again in a moment."
    )
