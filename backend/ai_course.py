"""
ai_course.py — iQmaxer AI Course Generator (OpenRouter + Solo Leveling themed)

Generates complete RPG-style courses with story-driven practical problems.
Uses OpenRouter to access GPT-4o or Claude Sonnet 4. Falls back to seeded
courses if the API key is missing or the request fails.
"""

import os
import json
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "") or os.environ.get("OPENCODE_GO_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "") or os.environ.get("OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go/v1")
DEFAULT_TIMEOUT = 300  # seconds (5 min — glm-5.2 is slow but reliable)

# Detect which provider we're using
_USING_OPENROUTER = bool(os.environ.get("OPENROUTER_API_KEY"))

if _USING_OPENROUTER:
    PRIMARY_MODEL = "openai/gpt-4o"
    FALLBACK_MODEL = "anthropic/claude-sonnet-4"
else:
    PRIMARY_MODEL = "glm-5.2"
    FALLBACK_MODEL = "deepseek-v4-pro"

RANK_ORDER = ["E", "D", "C", "B", "A", "S"]

# ── Solo Leveling System Prompt for Course Generation ──────────────────────

COURSE_SYSTEM_PROMPT = """You are the System from Solo Leveling, generating an RPG-style learning course for a Hunter (the user).

You must generate a complete course with practical, story-driven problems. Each problem is a "quest" that the Hunter must complete.

CRITICAL RULES:
1. Do NOT think step-by-step. Do NOT include reasoning or thinking sections.
2. Output ONLY the raw JSON object — no markdown, no code fences, no extra text.
3. Each quest MUST be a vivid, practical scenario with SPECIFIC NUMBERS and conditions
4. Use extreme/unusual settings: other planets, deep ocean, volcanoes, magical worlds, futuristic tech
5. Solo Leveling / RPG game style: the user is a "hunter" completing quests
6. Generate 3-5 quests spanning difficulty ranks E (easy) through A (hard)
7. Each quest MUST have a clear, unambiguous correct_answer (number or short string)
8. Problems can be from math, physics, chemistry, or any STEM field
9. Make problems practical and applied — NOT abstract theory

Output JSON schema:
{
  "title": "Course title (engaging, RPG-style)",
  "description": "Course description explaining the theme",
  "quests": [
    {
      "title": "Quest name",
      "story": "Vivid scenario description with specific numbers and conditions...",
      "topic": "STEM concept being taught",
      "difficulty_rank": "E|D|C|B|A|S",
      "xp_reward": 20,
      "correct_answer": "42.5",
      "hint": "Helpful hint showing the right approach"
    }
  ]
}

XP reward guidelines by rank: E=20, D=30, C=50, B=80, A=150, S=300

EXAMPLE of a good quest:
```json
{
  "title": "The Mars Oil Barrel Launch",
  "story": "You are a Hunter on Mars. A supply drop of a 100kg oil barrel must be launched from Point A (altitude 0m) to Point B (altitude 50m) which is 200m away horizontally. Mars gravity is 3.72 m/s². There is a headwind of 10 m/s opposing the launch. Assuming no air resistance on the barrel itself (only wind affects horizontal velocity), calculate the minimum initial velocity (in m/s) required to reach Point B. Round to 1 decimal place.",
  "topic": "Projectile Motion with Modified Gravity",
  "difficulty_rank": "D",
  "xp_reward": 30,
  "correct_answer": "32.5",
  "hint": "Use the projectile range equation: R = v²sin(2θ)/g. Since launch angle isn't specified, assume the optimal 45° for minimum velocity. Account for wind reducing horizontal component."
}
```"""


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
        "X-Title": "iQmaxer AI Course Generator",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": COURSE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.8,
        "max_tokens": 4000,
    }

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            logger.info(f"Calling {model} at {OPENROUTER_BASE_URL}/chat/completions")
            response = await client.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            # GLM-5.2 on OpenCode Go sometimes puts content in reasoning_content
            if not content or not content.strip():
                content = data["choices"][0]["message"].get("reasoning_content", "")
                if content:
                    logger.info(f"Extracted content from reasoning_content ({len(content)} chars)")
                    # Try to find JSON in reasoning_content (model may bury it)
                    import re
                    json_match = re.search(r'\{.*\}', content, re.DOTALL)
                    if json_match:
                        content = json_match.group(0)
                        logger.info("Extracted JSON from reasoning_content text")
            logger.info(f"OpenRouter ({model}) returned {len(content)} chars, finish={data['choices'][0].get('finish_reason','?')}")
            logger.info(f"First 200 chars: {repr(content[:200])}")
            return content
    except httpx.TimeoutException:
        logger.error(f"OpenRouter timeout on model {model}")
    except httpx.HTTPStatusError as e:
        logger.error(f"OpenRouter HTTP {e.response.status_code}: {e.response.text[:200]}")
    except (httpx.RequestError, json.JSONDecodeError, KeyError) as e:
        logger.error(f"OpenRouter error: {e}")

    return None


# ── JSON extraction / validation ───────────────────────────────────────────────

def _parse_course(raw: str) -> Optional[dict]:
    """
    Parse a course JSON from the LLM response.
    Handles markdown code fences and stray text.
    """
    text = raw.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        start = text.find("{")
        if start == -1:
            start = text.find("[")
        if start != -1:
            text = text[start:]
        if text.endswith("```"):
            text = text[:-3].strip()
        if text.endswith("`"):
            text = text[:-1].strip()

    try:
        course = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON: {e}")
        logger.error(f"Text starts with: {repr(text[:300])}")
        return None

    # Validate structure
    if not isinstance(course, dict):
        logger.error("Parsed course is not a dictionary")
        return None
    if "quests" not in course or not isinstance(course["quests"], list):
        logger.error("Parsed course missing 'quests' array")
        return None
    if not course.get("title"):
        logger.error("Parsed course missing 'title'")
        return None

    # Validate and normalize each quest
    validated_quests = []
    for i, q in enumerate(course["quests"]):
        if not isinstance(q, dict):
            continue

        rank = q.get("difficulty_rank", "E").upper()
        if rank not in RANK_ORDER:
            rank = "E"

        validated = {
            "title": str(q.get("title", f"Quest {i}")),
            "story": str(q.get("story", "")),
            "topic": str(q.get("topic", "")),
            "difficulty_rank": rank,
            "xp_reward": int(q.get("xp_reward", 20)),
            "correct_answer": str(q.get("correct_answer", "")),
            "hint": str(q.get("hint", "")),
            "order": i,
        }
        validated_quests.append(validated)

    course["quests"] = validated_quests
    return course


# ── Public API ─────────────────────────────────────────────────────────────────

async def generate_course(goal: str) -> dict:
    """
    Generate an AI-powered Solo Leveling style course with story-driven quests.

    Parameters
    ----------
    goal : str
        The user's learning goal (e.g. "хочу вышмат" / "want to learn advanced math")

    Returns
    -------
    dict
        {
            "title": str,
            "description": str,
            "quests": [
                {
                    "title": str,
                    "story": str,
                    "topic": str,
                    "difficulty_rank": str,
                    "xp_reward": int,
                    "correct_answer": str,
                    "hint": str,
                    "order": int,
                }
            ]
        }
    """
    logger.info(f"generate_course: goal='{goal}'")

    if not OPENROUTER_API_KEY:
        logger.warning("OPENROUTER_API_KEY not set")
        return {
            "title": "Error",
            "description": "OPENROUTER_API_KEY is not configured. Please set the environment variable to use AI course generation.",
            "quests": [],
        }

    prompt = f"""Generate a Solo Leveling RPG-style course for the following learning goal:

User's Goal: {goal}

Create engaging, story-driven practical problems that teach real STEM concepts through vivid scenarios.
The user is a Hunter in the Solo Leveling universe. Generate 3-5 quests with varying difficulty.

Remember:
- Each quest must have a vivid story with SPECIFIC NUMBERS
- Include the correct answer (number or short string)
- Problems should be practical and applied
- Cover multiple difficulty ranks from E to S
- Make it FUN and ENGAGING — like a real RPG quest chain.

CRITICAL: Output ONLY the raw JSON object. No thinking, no reasoning, no markdown, no code fences."""

        # Attempt with primary model
    logger.info(f"Starting AI generation for goal='{goal}' with model={PRIMARY_MODEL}")
    raw = await _call_openrouter(prompt, PRIMARY_MODEL)
    logger.info(f"Primary model returned: raw={'yes' if raw else 'no'} ({len(raw) if raw else 0} chars)")

    # Parse if we got something
    if raw:
        course = _parse_course(raw)
        if course is not None:
            logger.info(
                f"AI course generated: title='{course.get('title')}', "
                f"{len(course['quests'])} quests"
            )
            return course
        logger.warning("Failed to parse AI response")

    logger.error("All AI models failed for course generation")
    return {
        "title": "Generation Failed",
        "description": "Could not generate a course at this time. The AI service may be unavailable. Please try again later.",
        "quests": [],
    }


async def check_answer(quest: dict, user_answer: str) -> dict:
    """
    Check if the user's answer is correct via AI.
    Considers tolerance for numerical answers.

    Parameters
    ----------
    quest : dict
        The quest dict (must have title, story, correct_answer, topic, hint).
    user_answer : str
        The user's submitted answer.

    Returns
    -------
    dict
        {"is_correct": bool, "feedback": str}
    """
    if not OPENROUTER_API_KEY:
        # Simple fallback: exact or numeric comparison
        correct = quest.get("correct_answer", "").strip().lower()
        submitted = user_answer.strip().lower()

        # Try direct comparison
        if submitted == correct:
            return {
                "is_correct": True,
                "feedback": "Correct! Well done, Hunter!",
            }

        # Try numeric comparison with tolerance
        try:
            correct_num = float(correct.replace(",", "."))
            submitted_num = float(submitted.replace(",", "."))
            if abs(correct_num - submitted_num) / max(abs(correct_num), 1) < 0.05:
                return {
                    "is_correct": True,
                    "feedback": "Correct (within tolerance)! Good work, Hunter!",
                }
        except (ValueError, AttributeError):
            pass

        # Give a hint about the right approach
        hint = quest.get("hint", "Try reviewing the problem carefully.")
        return {
            "is_correct": False,
            "feedback": f"That's not quite right. {hint}",
        }

    prompt = f"""You are the System from Solo Leveling, checking a Hunter's answer to a quest.

Quest Title: {quest.get('title', 'Unknown')}
Quest Story: {quest.get('story', '')}
Topic: {quest.get('topic', '')}
Correct Answer: {quest.get('correct_answer', '')}
Hint: {quest.get('hint', '')}

User's Answer: {user_answer}

Determine if the user's answer is correct. For numerical answers, allow reasonable tolerance (±5% or small absolute difference).
Provide constructive, RPG-style feedback. If correct, praise the Hunter. If wrong, explain the right approach without giving the answer directly.

Respond in JSON format ONLY:
{{"is_correct": true/false, "feedback": "Your feedback here"}}"""

    raw = await _call_openrouter(prompt, PRIMARY_MODEL)
    if raw is None:
        raw = await _call_openrouter(prompt, FALLBACK_MODEL)

    if raw:
        try:
            text = raw.strip()
            if text.startswith("```"):
                start = text.find("{")
                if start != -1:
                    text = text[start:]
                if text.endswith("```"):
                    text = text[:-3].strip()
            result = json.loads(text)
            if "is_correct" in result and "feedback" in result:
                return result
        except (json.JSONDecodeError, KeyError):
            pass

    # Fallback
    return {
        "is_correct": False,
        "feedback": "The System could not evaluate your answer. Please try again.",
    }


async def generate_hint(quest: dict) -> str:
    """
    Generate a helpful hint that guides toward the answer without giving it away.

    Parameters
    ----------
    quest : dict
        The quest dict with title, story, topic, correct_answer, etc.

    Returns
    -------
    str
        A hint string.
    """
    # If there's already a hint, return it
    if quest.get("hint"):
        return quest["hint"]

    if not OPENROUTER_API_KEY:
        return "Try breaking the problem down step by step. What formulas or concepts apply here?"

    prompt = f"""You are the System from Solo Leveling, giving a Hunter a hint for a quest.

Quest Title: {quest.get('title', 'Unknown')}
Quest Story: {quest.get('story', '')}
Topic: {quest.get('topic', '')}
Correct Answer: {quest.get('correct_answer', '')}

Generate a helpful hint (2-3 sentences) that:
1. Guides the Hunter toward the right approach
2. Does NOT give away the exact answer
3. Is in the Solo Leveling RPG style
4. References the specific scenario and numbers

Respond with ONLY the hint text, no JSON, no markdown."""

    raw = await _call_openrouter(prompt, PRIMARY_MODEL)
    if raw is None:
        raw = await _call_openrouter(prompt, FALLBACK_MODEL)

    if raw:
        # Clean up any markdown or JSON wrapping
        hint = raw.strip()
        if hint.startswith("```"):
            hint = hint.strip("`").strip()
        if hint.startswith('"') and hint.endswith('"'):
            try:
                hint = json.loads(hint)
            except json.JSONDecodeError:
                pass
        return hint

    return "Try reviewing the problem carefully. What concepts from this topic apply to the given numbers?"
