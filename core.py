"""Core shopping-list calculations; intentionally dependency-free."""
from math import ceil, isfinite
import json

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
    result['unit'] = normalise_unit(result.get('unit', 'each'))
    if 'purchase_unit' in result:
        result['purchase_unit'] = normalise_unit(result['purchase_unit'])
    result.setdefault("aisle", aisle_for(result.get("name", "")))
    result.setdefault("checked", False)
    result.setdefault("notes", "")
    return result


def normalise_unit(unit):
    unit = str(unit).strip().lower()
    aliases = {'cups': 'cup', 'cans': 'can', 'packs': 'pack', 'bags': 'bag', 'jars': 'jar', 'bunches': 'bunch', 'bulbs': 'bulb', 'heads': 'head', 'bottles': 'bottle', 'tubes': 'tube', 'pieces': 'each', 'piece': 'each', 'cloves': 'each', 'clove': 'each'}
    return aliases.get(unit, unit)


def positive_number(value, field):
    try:
        valid = type(value) in (int, float) and isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f'{field} must be a finite positive number')
    return value


def presentation(shopping_list: dict) -> dict:
    servings = positive_number(shopping_list.get('servings', 4), 'servings')
    base = positive_number(shopping_list.get('base_servings', 4), 'base_servings')
    result = {**shopping_list, 'items': []}
    for item in shopping_list.get('items', []):
        amount, unit = scaled_purchase_quantity(item, servings, base)
        result['items'].append({**item, 'display_quantity': amount, 'display_unit': unit})
    json.dumps(result, allow_nan=False)
    return result


def scaled_purchase_quantity(item: dict, servings: int, base_servings: int = 4) -> tuple[int | float, str]:
    """Return a shopper-friendly quantity, always rounding whole shopping units up."""
    quantity = positive_number(item['quantity'], 'quantity') * positive_number(servings, 'servings') / positive_number(base_servings, 'base_servings')
    positive_number(quantity, 'scaled quantity')
    if 'package_size' in item:
        quantity = quantity / positive_number(item['package_size'], 'package_size')
        positive_number(quantity, 'purchase quantity')
        return ceil(quantity), normalise_unit(item.get('purchase_unit', 'pack'))
    unit = normalise_unit(item.get("unit", "each"))
    if unit in {"each", "bunch", "pack", "bag", "jar", "can", "bulb", "head", "piece", "bottle", "tube"}:
        return ceil(quantity), unit
    return round(quantity, 2), unit
