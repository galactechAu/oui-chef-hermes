"""Structured-response handling and prompt construction for meal generation."""
import ipaddress
import json
import re
from urllib.parse import urlparse

from allergies import allergy_error, detect_allergens, normalise_allergens

FORBIDDEN = ("mushroom",)


def extract_json(text: str):
    """Extract the first complete JSON object/array from noisy CLI output."""
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    for start, char in enumerate(text):
        if char not in "{[":
            continue
        stack, quoted, escaped = [], False, False
        for end in range(start, len(text)):
            current = text[end]
            if quoted:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    quoted = False
                continue
            if current == '"':
                quoted = True
            elif current in "{[":
                stack.append(current)
            elif current in "}]":
                if not stack or {"}": "{", "]": "["}[current] != stack.pop():
                    break
                if not stack:
                    try:
                        return json.loads(text[start:end + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError("generation did not return valid JSON")


def validate_generation(payload: dict, allergies=None) -> list[dict]:
    allergies = normalise_allergens(allergies or ["mushrooms"])
    meals = payload.get("meals") if isinstance(payload, dict) else None
    if not isinstance(meals, list) or not meals:
        raise ValueError("generation must contain a non-empty meals array")
    clean = []
    for meal in meals:
        if not isinstance(meal, dict) or not meal.get("name") or not isinstance(meal.get("ingredients"), list):
            raise ValueError("every generated meal needs a name and ingredients")
        source_url = str(meal.get("source_url", "")).strip()
        parsed = urlparse(source_url)
        try:
            host_is_public = not ipaddress.ip_address(parsed.hostname or "").is_private and not ipaddress.ip_address(parsed.hostname or "").is_loopback
        except ValueError:
            host_is_public = bool(parsed.hostname) and parsed.hostname.lower() != "localhost" and not parsed.hostname.lower().endswith((".local", ".internal", ".lan", ".ts.net"))
        if parsed.scheme not in {"http", "https"} or not host_is_public or not str(meal.get("source_title", "")).strip():
            raise ValueError("every generated meal needs a valid public recipe source URL and title")
        meal_text = " ".join([str(meal.get(key, "")) for key in ("name", "description", "nutrition_note")] + [str(item.get("name", "")) for item in meal["ingredients"]] + [str(step) for step in meal.get("method", [])])
        matches = detect_allergens(meal_text, allergies)
        if matches: raise allergy_error(matches)
        for item in meal["ingredients"]:
            name = str(item.get("name", ""))
            if not name or not isinstance(item.get("quantity"), (int, float)) or not item.get("unit"):
                raise ValueError("every ingredient needs name, numeric quantity, and unit")
        meal.setdefault("method", [])
        meal.setdefault("complexity", "Easy")
        clean.append(meal)
    return clean


def verify_source_evidence(meal: dict, page_text: str) -> bool:
    """Require multiple source-title tokens and a meaningful token for every ingredient."""
    haystack = " ".join(re.findall(r"[a-z0-9]+", str(page_text).casefold()))
    title_words = [word for word in re.findall(r"[a-z0-9]+", str(meal.get("source_title", "")).casefold()) if len(word) >= 4]
    if len(title_words) < 2 or sum(word in haystack for word in title_words) < 2:
        return False
    for ingredient in meal.get("ingredients", []):
        words = [word for word in re.findall(r"[a-z0-9]+", str(ingredient.get("name", "")).casefold()) if len(word) >= 4]
        if words and not any(word in haystack for word in words):
            return False
    return True


def generation_prompt(count: int, instruction: str, complexity: str, history: list[dict], exclusions: dict[str, set[str]] | None = None, positive_ratings: list[dict] | None = None, allergies=None) -> str:
    allergies = normalise_allergens(allergies or ["mushrooms"])
    exclusions = exclusions or {"names": set(), "urls": set()}
    positive_ratings = positive_ratings or []
    taste_signals = "; ".join(f"{row['name']} ({row['rating']}★)" for row in positive_ratings) or "None"
    previous = "; ".join(f"{row['name']} (rating {row.get('rating', 'unrated')})" for row in history[-30:]) or "None"
    excluded = "; ".join(sorted(exclusions["names"])) or "None"
    return f'''Generate exactly {count} distinct dinner recipes for two adults plus two leftover lunches (4 serves).\nUser instruction: {instruction or "No further instruction."}\nComplexity: {complexity}. Use only normal Australian home-kitchen equipment and Coles/Woolworths ingredients.\nHard constraints: healthy fat-loss focused, high protein, low added sugar, controlled carbs, no dietary allergens ({'; '.join(allergies)}) in any form, no rich creamy sauces, no buns/fries/normal pasta unless the user explicitly requests them. Prioritise chicken breast, lean beef, salmon, legumes and vegetables. Never repeat or closely imitate low-rated meals. Prefer patterns from 5-rated meals. Positive household taste signals (3–5 stars; use as flavour/style guidance only and do not repeat these meals): {taste_signals}. Existing meal history: {previous}. Previously generated/saved names that must not be suggested again: {excluded}.\nReturn ONLY compact valid JSON, under 7000 characters: {{"meals":[{{"name":"", "source_url":"https://public-recipe-page", "source_title":"Published recipe title", "description":"", "complexity":"Easy", "ingredients":[{{"name":"", "quantity":1, "unit":"g", "aisle":"Fresh produce", "notes":""}}], "method":["short source-supported step"], "nutrition_note":""}}]}}. First use web/browser tools to find real public recipe pages. Every meal must be grounded in one distinct inspected public source URL and title; never invent a recipe, ingredients, method, title, or URL. If fewer than {count} compliant unique recipes are available, return an error JSON rather than making up substitutes. Do not use login-only, paywalled, private-network, or access-restricted sources.'''
