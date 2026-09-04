"""Allergy-term normalisation and recipe-text screening."""
from __future__ import annotations

import re

BASELINE_ALLERGENS = ("mushrooms",)


def normalise_allergens(values) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        raise ValueError("dietary allergies must be a list")
    clean: list[str] = []
    for value in values:
        term = re.sub(r"\s+", " ", str(value).strip().casefold())
        if not term or len(term) > 80 or not re.fullmatch(r"[a-z0-9][a-z0-9 &'/-]*", term):
            raise ValueError("each dietary allergy must be a short ingredient or phrase")
        if term not in clean:
            clean.append(term)
    for baseline in reversed(BASELINE_ALLERGENS):
        if baseline not in clean:
            clean.insert(0, baseline)
    return clean


def detect_allergens(text: str, allergens) -> list[str]:
    haystack = str(text or "").casefold()
    matches: list[str] = []
    for allergen in normalise_allergens(allergens):
        variants = [allergen]
        if allergen.endswith("s") and len(allergen) > 3:
            variants.append(allergen[:-1])
        if any(re.search(r"(?<![a-z0-9])" + re.escape(value) + r"(?![a-z0-9])", haystack) for value in variants):
            matches.append(allergen)
    return matches


def allergy_error(matches: list[str]) -> ValueError:
    return ValueError("dietary allergen detected: " + ", ".join(matches))
