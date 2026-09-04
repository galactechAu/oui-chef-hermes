"""Core shopping-list calculations; intentionally dependency-free."""
from math import ceil

DEFAULT_AISLE = "Pantry"
AISLES_BY_KEYWORD = {
    "milk": "Dairy & fridge", "yoghurt": "Dairy & fridge", "yogurt": "Dairy & fridge",
    "cheese": "Dairy & fridge", "butter": "Dairy & fridge", "cream": "Dairy & fridge",
    "chicken": "Meat & seafood", "beef": "Meat & seafood", "salmon": "Meat & seafood",
    "mince": "Meat & seafood", "fish": "Meat & seafood",
    "tomato": "Fresh produce", "onion": "Fresh produce", "garlic": "Fresh produce",
    "ginger": "Fresh produce", "capsicum": "Fresh produce", "broccoli": "Fresh produce",
    "beans": "Fresh produce", "zucchini": "Fresh produce", "carrot": "Fresh produce",
    "lettuce": "Fresh produce", "avocado": "Fresh produce", "spinach": "Fresh produce",
    "coriander": "Fresh produce", "lime": "Fresh produce", "lemon": "Fresh produce",
    "cauliflower": "Fresh produce", "cucumber": "Fresh produce", "banana": "Fresh produce",
}


def aisle_for(name: str) -> str:
    lowered = name.lower()
    return next((aisle for key, aisle in AISLES_BY_KEYWORD.items() if key in lowered), DEFAULT_AISLE)


def normalise_item(item: dict) -> dict:
    result = dict(item)
    result.setdefault("aisle", aisle_for(result.get("name", "")))
    result.setdefault("checked", False)
    result.setdefault("notes", "")
    return result


def scaled_purchase_quantity(item: dict, servings: int, base_servings: int = 4) -> tuple[int | float, str]:
    """Return a shopper-friendly quantity, always rounding whole shopping units up."""
    quantity = float(item["quantity"]) * servings / base_servings
    if item.get("package_size"):
        quantity = ceil(quantity / float(item["package_size"]))
        return quantity, item.get("purchase_unit", "pack")
    unit = item.get("unit", "each")
    if unit in {"each", "bunch", "pack", "bag", "jar", "can", "bulb", "head", "piece", "bottle", "tube"}:
        return ceil(quantity), unit
    return round(quantity, 2), unit
