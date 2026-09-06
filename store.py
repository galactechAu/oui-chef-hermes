"""Small JSON-backed store for meal drafts, ratings, and shopping lists."""
import json
import re
import threading
from functools import wraps
from fractions import Fraction
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.parse import urlparse
from uuid import uuid4, uuid5, NAMESPACE_URL

from allergies import BASELINE_ALLERGENS, normalise_allergens, detect_allergens, allergy_error
from math import isfinite
from core import normalise_item, normalise_unit, presentation, positive_number


def transaction(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return locked


class ShoppingStore:
    def __init__(self, path: Path, publish=None):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._publish = publish or (lambda *_args, **_kwargs: None)

    def _load(self) -> dict:
        data = json.loads(self.path.read_text()) if self.path.exists() else {}
        data.setdefault("lists", [])
        data.setdefault("drafts", [])
        data.setdefault("meal_ratings", {})
        data.setdefault("imported_recipes", [])
        data.setdefault("import_jobs", [])
        data.setdefault("aisle_order", [])
        data.setdefault("generation_candidates", [])
        data.setdefault("generation_jobs", [])
        data.setdefault("household_preferences", {})
        data["household_preferences"]["dietary_allergies"] = normalise_allergens(data["household_preferences"].get("dietary_allergies", BASELINE_ALLERGENS))
        for candidate in data["generation_candidates"]:
            candidate.setdefault("source_domain", (urlparse(candidate.get("canonical_source_url", "")).hostname or "").lower())
            candidate.setdefault("last_shown_at", candidate.get("created_at", ""))
            candidate.setdefault("selected", False); candidate.setdefault("saved_meal_id", None); candidate.setdefault("recipe", {})
        data.setdefault("calendar", [])
        data.setdefault("recipe_books", [])
        data.setdefault("meal_catalog", [])
        known_catalogue = {row.get("recipe_id"): row for row in data["meal_catalog"] if row.get("recipe_id")}
        for recipe in data["imported_recipes"]:
            if recipe["id"] not in known_catalogue:
                data["meal_catalog"].append({"id": f"meal-{uuid5(NAMESPACE_URL, 'oui-chef:'+recipe['id']).hex}", "recipe_id": recipe["id"]})
        recipe_to_meal = {row["recipe_id"]: row["id"] for row in data["meal_catalog"] if row.get("recipe_id")}
        known_ids = {row['id'] for row in data['meal_catalog']}
        for listing in data['lists']:
            for index, meal in enumerate(listing.get('meals', [])):
                alias = f"list:{listing['id']}:{index}"
                # Add a stable reference, never replace legacy IDs or recipe payloads.
                if not meal.get('canonical_id'):
                    seed = 'oui-chef:' + alias
                    candidate = f"meal-{uuid5(NAMESPACE_URL, seed).hex}"
                    revision = 0
                    while candidate in known_ids:
                        revision += 1
                        candidate = f"meal-{uuid5(NAMESPACE_URL, seed+':'+str(revision)).hex}"
                    meal['canonical_id'] = candidate
                mid = meal['canonical_id']
                if mid not in known_ids:
                    data['meal_catalog'].append({'id': mid, 'list_id': listing['id'], 'list_meal_key': mid})
                    known_ids.add(mid)
                recipe_to_meal[alias] = mid
        data.setdefault("recipe_book_memberships", [])
        for membership in data["recipe_book_memberships"]:
            membership["meal_id"] = recipe_to_meal.get(membership.get("meal_id"), membership.get("meal_id"))
        return data

    @transaction
    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
        temp.replace(self.path)
        self._publish("state.changed")

    def get_dietary_allergies(self) -> list[str]:
        return list(self._load()["household_preferences"]["dietary_allergies"])

    def set_dietary_allergies(self, allergies) -> list[str]:
        clean = normalise_allergens(allergies)
        with self._lock:
            data = self._load(); data["household_preferences"]["dietary_allergies"] = clean; self._save(data)
        return clean

    def get_lists(self) -> list[dict]:
        return self._load()["lists"]

    def get_aisle_order(self) -> list[str]:
        return self._load()["aisle_order"]

    def set_aisle_order(self, order: list[str]) -> list[str]:
        if not isinstance(order, list) or any(not isinstance(value, str) or not value.strip() for value in order):
            raise ValueError("aisle order must be a list of aisle names")
        cleaned = list(dict.fromkeys(value.strip() for value in order))
        with self._lock:
            data = self._load(); data["aisle_order"] = cleaned; self._save(data)
        return cleaned

    def get_list(self, list_id: str) -> dict | None:
        return next((item for item in self.get_lists() if item["id"] == list_id), None)

    def get_meal_catalogue(self) -> list[dict]:
        return self._catalogue(self._load())

    def _catalogue(self, data: dict) -> list[dict]:
        recipes = {row['id']: row for row in data['imported_recipes']}
        listings = {row['id']: row for row in data['lists']}
        result = []
        for row in data['meal_catalog']:
            if row.get('recipe_id'):
                imported = recipes.get(row['recipe_id'])
                if not imported: continue
                recipe = imported.get('recipe', {})
                source = imported.get('source', {})
                extra = {'kind': 'imported', 'url': f"/api/import-recipes/{row['recipe_id']}"}
            else:
                listing = listings.get(row.get('list_id'), {})
                found = next(((i, m) for i, m in enumerate(listing.get('meals', [])) if m.get('canonical_id') == row['id']), None)
                if not found: continue
                index, meal = found
                recipe = {**{key: value for key, value in meal.items() if key not in {'recipe', 'canonical_id'}}, **(meal.get('recipe') or {})}
                recipe.setdefault('name', meal.get('name', ''))
                recipe.setdefault('steps', recipe.get('method', []))
                source = meal.get('source') or {'url': meal.get('url', meal.get('source_url', ''))}
                extra = {'kind': 'list', 'list_id': row['list_id'], 'meal_index': index, 'url': f"/api/lists/{row['list_id']}/recipes/{index}"}
            name = recipe.get('name', '')
            result.append({**row, **extra, 'canonical_id': row['id'], 'recipe': recipe, 'source': source, 'name': name, 'description': recipe.get('summary', ''), 'image_url': recipe.get('image_url', ''), 'rating': data['meal_ratings'].get(name), 'book_ids': list(dict.fromkeys(m['book_id'] for m in data['recipe_book_memberships'] if m['meal_id'] == row['id'] and any(b['id'] == m['book_id'] for b in data['recipe_books'])))})
        return result

    def get_imported_recipes(self) -> list[dict]:
        return self._load()["imported_recipes"]

    def get_imported_recipe(self, recipe_id: str) -> dict | None:
        return next((item for item in self.get_imported_recipes() if item["id"] == recipe_id), None)

    def _present_recipe_book(self, data: dict, book: dict) -> dict:
        ids = [row["meal_id"] for row in data["recipe_book_memberships"] if row["book_id"] == book["id"]]
        ids = list(dict.fromkeys(ids))
        catalogue = {row['id']: row for row in self._catalogue(data)}
        meals = [catalogue[meal_id] for meal_id in ids if meal_id in catalogue]
        cover = next((m['image_url'] for m in meals if str(m['image_url']).startswith(('https://', 'http://'))), '')
        return {'pinned': False, 'updated_at': book.get('created_at', ''), 'last_used_at': '', **book, 'meal_count': len(meals), 'meal_ids': ids, 'missing_meal_ids': [mid for mid in ids if mid not in catalogue], 'cover_url': cover, 'cover_fallback': not bool(cover), 'meal_preview': [m['name'] for m in meals[:3]], 'matching_meal_titles': [], 'meals': meals}

    def get_recipe_books(self, query: str = "") -> list[dict]:
        data = self._load()
        needle = str(query).casefold().strip()
        books = []
        for book in data['recipe_books']:
            row = self._present_recipe_book(data, book)
            matches = [m['name'] for m in row['meals'] if needle and needle in m['name'].casefold()]
            row['matching_meal_titles'] = matches[:5]
            if not needle or needle in row['title'].casefold() or matches: books.append(row)
        return sorted(books, key=lambda b: (bool(b['pinned']), b['updated_at'], b['id']), reverse=True)

    def get_recent_recipe_books(self, limit: int = 10) -> list[dict]:
        books = self.get_recipe_books()
        return sorted(books, key=lambda b: (b['last_used_at'] or b['updated_at'], b['id']), reverse=True)[:max(1, min(100, int(limit)))]

    @staticmethod
    def _touch_book(book):
        book['updated_at'] = book['last_used_at'] = datetime.now(timezone.utc).isoformat()

    def use_recipe_book(self, book_id: str) -> dict:
        with self._lock:
            data = self._load()
            book = next((b for b in data['recipe_books'] if b['id'] == book_id), None)
            if not book: raise KeyError(book_id)
            book['last_used_at'] = datetime.now(timezone.utc).isoformat()
            self._save(data)
            return self._present_recipe_book(data, book)

    def pin_recipe_book(self, book_id: str, pinned: bool) -> dict:
        if not isinstance(pinned, bool): raise ValueError('pinned must be a boolean')
        with self._lock:
            data = self._load()
            book = next((b for b in data['recipe_books'] if b['id'] == book_id), None)
            if not book: raise KeyError(book_id)
            book['pinned'] = pinned
            self._touch_book(book)
            self._save(data)
            return self._present_recipe_book(data, book)

    def get_recipe_book(self, book_id: str) -> dict | None:
        data = self._load(); book = next((row for row in data["recipe_books"] if row["id"] == book_id), None)
        return self._present_recipe_book(data, book) if book else None

    def create_recipe_book(self, title: str) -> dict:
        clean = re.sub(r"\s+", " ", str(title).strip())
        if not clean or len(clean) > 80: raise ValueError("recipe book title must be 1–80 characters")
        with self._lock:
            data = self._load()
            if any(row["title"].casefold() == clean.casefold() for row in data["recipe_books"]): raise ValueError("a recipe book with that title already exists")
            book = {"id": f"book-{uuid4().hex}", "title": clean, "created_at": datetime.now(timezone.utc).isoformat()}
            data["recipe_books"].insert(0, book); self._save(data); return self._present_recipe_book(data, book)

    def rename_recipe_book(self, book_id: str, title: str) -> dict:
        clean = re.sub(r"\s+", " ", str(title).strip())
        if not clean or len(clean) > 80: raise ValueError("recipe book title must be 1–80 characters")
        with self._lock:
            data = self._load(); book = next((row for row in data["recipe_books"] if row["id"] == book_id), None)
            if not book: raise KeyError(book_id)
            if any(row["id"] != book_id and row["title"].casefold() == clean.casefold() for row in data["recipe_books"]): raise ValueError("a recipe book with that title already exists")
            book["title"] = clean; self._touch_book(book); self._save(data); return self._present_recipe_book(data, book)

    def create_list_from_recipe_book(self, book_id: str, meal_ids: list[str] | None = None, name: str = "", list_id: str = "", servings: int | None = None) -> dict:
        # One lock, one snapshot, one durable write: no partial append on failure.
        with self._lock:
            data = self._load()
            book = next((b for b in data['recipe_books'] if b['id'] == book_id), None)
            if not book: raise KeyError(book_id)
            member_ids = {m['meal_id'] for m in data['recipe_book_memberships'] if m['book_id'] == book_id}
            selected = sorted(member_ids) if meal_ids is None or meal_ids == [] else self._resolve_ids(data, meal_ids)
            if not selected or any(mid not in member_ids for mid in selected):
                raise ValueError('select one or more meals from this Recipe Book')
            catalogue = {m['id']: m for m in self._catalogue(data)}
            if any(mid not in catalogue for mid in selected): raise ValueError('a selected Meal source is missing')
            result = self._assemble_recipes(data, [catalogue[mid] for mid in selected], list_id, name, servings)
            self._touch_book(book)
            self._save(data)
            return {**result, 'selected_count': len(selected)}

    def _assemble_recipes(self, data, selected, list_id='', name='', servings=None):
        listing = next((row for row in data['lists'] if row['id'] == list_id), None) if list_id else None
        if list_id and listing is None: raise KeyError(list_id)
        target_servings = servings if servings is not None else (listing.get('servings', 4) if listing else 4)
        if isinstance(target_servings, bool) or not isinstance(target_servings, int) or not 1 <= target_servings <= 10:
            raise ValueError('servings must be a whole number from 1 to 10')
        if listing is None:
            listing = {'id': f'{date.today().isoformat()}-{uuid4().hex[:8]}', 'name': name.strip() or 'Selected meals — shopping list', 'date': date.today().isoformat(), 'base_servings': 4, 'servings': target_servings, 'meals': [], 'items': []}
        base = listing.get('base_servings', 4)
        positive_number(base, 'base_servings')
        presentation(listing)
        incoming, meals = [], []
        for saved in selected:
            recipe = saved['recipe']
            if not recipe.get('name') or not isinstance(recipe.get('ingredients'), list) or not recipe['ingredients']:
                raise ValueError('this meal does not have recipe ingredients yet')
            screening = recipe
            if saved.get('kind') == 'list':
                origin = next(row for row in data['lists'] if row['id'] == saved['list_id'])
                screening = origin['meals'][saved['meal_index']]
            matches = detect_allergens(json.dumps(screening, ensure_ascii=False), data['household_preferences']['dietary_allergies'])
            if matches: raise allergy_error(matches)
            recipe_servings = recipe.get('servings', recipe.get('base_servings', 4))
            if isinstance(recipe_servings, bool) or not isinstance(recipe_servings, (int, float)) or not isfinite(recipe_servings) or recipe_servings <= 0:
                raise ValueError('recipe servings must be a positive number')
            for item in self._recipe_items(recipe['ingredients']):
                quantity = item.get('quantity', 1)
                if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or not isfinite(quantity) or quantity <= 0 or not str(item.get('name', '')).strip():
                    raise ValueError('ingredient name and positive quantity are required')
                if 'package_size' in item:
                    package = item['package_size']
                    if isinstance(package, bool) or not isinstance(package, (int, float)) or not isfinite(package) or package <= 0:
                        raise ValueError('ingredient package_size must be a positive number')
                item['quantity'] = quantity * base / recipe_servings
                incoming.append(item)
            meals.append({'name': recipe['name'], 'recipe': recipe, 'source': saved.get('source', {}), 'url': saved.get('source', {}).get('url', ''), 'canonical_id': saved['id']})
        def key(item):
            return (re.sub(r'\s+', ' ', str(item.get('name', '')).strip()).casefold(), normalise_unit(item.get('unit', 'each')), item.get('package_size'), normalise_unit(item['purchase_unit']) if 'purchase_unit' in item else None)
        # Preserve IDs, custom aisles, notes and unrelated checked items.
        grouped = {key(item): item for item in listing.get('items', [])}
        for item in incoming:
            existing = grouped.get(key(item))
            if existing is None:
                listing.setdefault('items', []).append(item)
                grouped[key(item)] = item
            else:
                existing['quantity'] += item['quantity']
                existing['checked'] = False
                if item.get('notes') and item['notes'] not in existing.get('notes', ''):
                    existing['notes'] = '; '.join(filter(None, [existing.get('notes'), item['notes']]))
        listing.setdefault('meals', []).extend(meals)
        listing['servings'] = target_servings
        presentation(listing)
        if not list_id: data['lists'].insert(0, listing)
        return listing

    def delete_recipe_book(self, book_id: str) -> None:
        with self._lock:
            data = self._load(); before = len(data["recipe_books"]); data["recipe_books"] = [row for row in data["recipe_books"] if row["id"] != book_id]
            if len(data["recipe_books"]) == before: raise KeyError(book_id)
            data["recipe_book_memberships"] = [row for row in data["recipe_book_memberships"] if row["book_id"] != book_id]; self._save(data)

    @staticmethod
    def _ids(values):
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v for v in values):
            raise ValueError('select one or more IDs')
        return list(dict.fromkeys(values))

    def _resolve_ids(self, data, values):
        aliases = {m['recipe_id']: m['id'] for m in data['meal_catalog'] if m.get('recipe_id')}
        aliases.update({f"list:{m['list_id']}:{m['meal_index']}": m['id'] for m in self._catalogue(data) if m['kind'] == 'list'})
        return list(dict.fromkeys(aliases.get(v, v) for v in self._ids(values)))

    def add_meals_to_recipe_book(self, book_id: str, meal_ids: list[str]) -> dict:
        return self.add_meals_to_recipe_books([book_id], meal_ids)[0]

    def add_meals_to_recipe_books(self, book_ids: list[str], meal_ids: list[str]) -> list[dict]:
        with self._lock:
            data = self._load()
            ids = self._ids(book_ids)
            books = {b['id']: b for b in data['recipe_books']}
            for bid in ids:
                if bid not in books: raise KeyError(bid)
            selected = self._resolve_ids(data, meal_ids)
            available = {m['id'] for m in self._catalogue(data)}
            if any(mid not in available for mid in selected): raise ValueError('select one or more saved Meals')
            known = {(m['book_id'], m['meal_id']) for m in data['recipe_book_memberships']}
            for bid in ids:
                for mid in selected:
                    if (bid, mid) not in known:
                        data['recipe_book_memberships'].append({'book_id': bid, 'meal_id': mid})
                self._touch_book(books[bid])
            self._save(data)
            return [self._present_recipe_book(data, books[bid]) for bid in ids]

    def remove_meal_from_recipe_book(self, book_id: str, meal_id: str) -> dict:
        with self._lock:
            data = self._load(); book = next((row for row in data["recipe_books"] if row["id"] == book_id), None)
            if not book: raise KeyError(book_id)
            meal_id = self._resolve_ids(data, [meal_id])[0]
            data["recipe_book_memberships"] = [row for row in data["recipe_book_memberships"] if not (row["book_id"] == book_id and row["meal_id"] == meal_id)]
            self._touch_book(book)
            self._save(data); return self._present_recipe_book(data, book)

    def get_uncategorised_meals(self) -> list[dict]:
        return [row for row in self._catalogue(self._load()) if not row['book_ids']]

    @transaction
    def save_imported_recipe(self, recipe: dict, source: dict) -> dict:
        if not isinstance(recipe, dict) or not str(recipe.get("name", "")).strip() or not isinstance(recipe.get("ingredients"), list) or not isinstance(recipe.get("steps"), list):
            raise ValueError("a recipe name, ingredients, and steps are required")
        data = self._load()
        entry = {"id": uuid4().hex, "recipe": recipe, "source": source}
        data["imported_recipes"].insert(0, entry)
        self._save(data)
        return entry

    def _recipe_items(self, ingredients: list) -> list[dict]:
        rows = []
        for ingredient in ingredients:
            if isinstance(ingredient, dict):
                raw = ingredient
            else:
                text = str(ingredient).strip()
                match = re.match(r"^(?P<quantity>\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s*(?:(?P<unit>kg|g|ml|l|tbsp|tsp|cups?|cans?|packs?|cloves?|each|pieces?|bags?|jars?|bunch(?:es)?|bulbs?|heads?|bottles?|tubes?)\b\s*)?(?P<name>.*)$", text, re.I)
                quantity, unit, name = 1, "each", text
                if match and match.group("name"):
                    value = match.group("quantity")
                    try:
                        quantity = float(sum(Fraction(part) for part in value.split()))
                    except (ValueError, ZeroDivisionError):
                        raise ValueError('invalid ingredient quantity')
                    unit, name = (match.group("unit") or "each").lower(), match.group("name").strip(" ,-")
                    if unit.rstrip("s") in {"clove", "piece"}: unit = "each"
                raw = {"name": name, "quantity": quantity, "unit": unit, "notes": text if text != name else ""}
            item = normalise_item(raw)
            item["id"] = f"recipe-{uuid4().hex}"
            item["checked"] = False
            rows.append(item)
        return rows

    def _add_recipe_to_list(self, recipe: dict, source: dict, list_id: str = "", name: str = "") -> dict:
        with self._lock:
            data = self._load()
            selected = {'id': f'meal-{uuid4().hex}', 'recipe': recipe, 'source': source}
            result = self._assemble_recipes(data, [selected], list_id, name or f"{recipe.get('name', '')} — shopping list")
            self._save(data)
            return result

    def add_imported_recipe_to_list(self, recipe_id: str, list_id: str = "", name: str = "") -> dict:
        with self._lock:
            data = self._load()
            saved = next((m for m in self._catalogue(data) if m.get('recipe_id') == recipe_id), None)
            if saved is None: raise KeyError(recipe_id)
            result = self._assemble_recipes(data, [saved], list_id, name or f"{saved['name']} — shopping list")
            self._save(data)
            return result

    def add_list_meal_to_list(self, source_list_id: str, meal_index: int, list_id: str = "", name: str = "") -> dict:
        with self._lock:
            data = self._load()
            origin = next((row for row in data['lists'] if row['id'] == source_list_id), None)
            if origin is None: raise KeyError(source_list_id)
            if type(meal_index) is not int or meal_index < 0: raise KeyError(meal_index)
            try: meal = origin['meals'][meal_index]
            except (IndexError, TypeError): raise KeyError(meal_index)
            recipe = {**{k: v for k, v in meal.items() if k not in {'recipe', 'canonical_id'}}, **(meal.get('recipe') or {})}
            recipe.setdefault('name', meal.get('name', ''))
            saved = {'id': meal['canonical_id'], 'kind': 'list', 'list_id': source_list_id, 'meal_index': meal_index, 'recipe': recipe, 'source': meal.get('source') or {'url': meal.get('url', '')}}
            result = self._assemble_recipes(data, [saved], list_id, name or f"{recipe['name']} — shopping list")
            self._save(data)
            return result

    def save_generation_job_meals(self, job_id: str, selected: list[int]) -> list[dict]:
        with self._lock:
            data = self._load(); job = next((row for row in data["generation_jobs"] if row["id"] == job_id), None)
            if not job or job.get("status") != "review": raise ValueError("generation job is not ready to add to Meals")
            choices = sorted({int(index) for index in selected if isinstance(index, int) or str(index).isdigit()})
            if not choices: raise ValueError("select at least one generated meal")
            meals = job.get("meals", []); saved = []
            for index in choices:
                if index < 0 or index >= len(meals): raise ValueError("invalid generated meal selection")
                meal = meals[index]; recipe = {"name": meal["name"], "summary": meal.get("description", meal.get("nutrition_note", "")), "complexity": meal.get("complexity", "Easy"), "ingredients": meal["ingredients"], "steps": meal.get("method", [])}
                entry = {"id": uuid4().hex, "recipe": recipe, "source": {"type": "generated", "label": meal.get("source_title", "Public recipe"), "url": meal.get("source_url", "")}}
                data["imported_recipes"].insert(0, entry); saved.append(entry)
            job.update({"status": "saved", "progress": 100, "stage": "Added to Meals", "saved_recipe_ids": [row["id"] for row in saved], "updated_at": datetime.now(timezone.utc).isoformat()})
            self._save(data); return saved

    def dismiss_generation_job_meal(self, job_id: str, index: int) -> dict:
        with self._lock:
            data = self._load(); job = next((row for row in data["generation_jobs"] if row["id"] == job_id), None)
            if not job or job.get("status") != "review": raise ValueError("generation job is not ready to review")
            try: job["meals"].pop(index)
            except (IndexError, TypeError): raise ValueError("generated meal was already dismissed")
            job["updated_at"] = datetime.now(timezone.utc).isoformat(); self._save(data); return job

    def create_list_from_meals(self, meal_ids: list[str], name: str = '', list_id: str = '', servings: int | None = None) -> dict:
        with self._lock:
            data = self._load()
            ids = self._resolve_ids(data, meal_ids)
            catalogue = {row['id']: row for row in self._catalogue(data)}
            if any(mid not in catalogue for mid in ids): raise ValueError('select one or more saved Meals')
            result = self._assemble_recipes(data, [catalogue[mid] for mid in ids], list_id, name, servings)
            self._save(data)
            return {**result, 'selected_count': len(ids)}

    def create_list_from_imported_recipes(self, recipe_ids: list[str], name: str = "") -> dict:
        with self._lock:
            data = self._load()
            ids = self._ids(recipe_ids)
            selected = [row for row in self._catalogue(data) if row.get('recipe_id') in ids]
            if len(selected) != len(ids): raise ValueError('select one or more saved Meals')
            result = self._assemble_recipes(data, selected, name=name)
            self._save(data)
            return result

    def delete_imported_recipe(self, recipe_id: str) -> None:
        with self._lock:
            data = self._load()
            before = len(data["imported_recipes"])
            data["imported_recipes"] = [row for row in data["imported_recipes"] if row["id"] != recipe_id]
            if len(data["imported_recipes"]) == before: raise KeyError(recipe_id)
            self._save(data)

    def delete_import_job(self, job_id: str) -> None:
        with self._lock:
            data = self._load()
            job = next((row for row in data["import_jobs"] if row["id"] == job_id), None)
            if not job: raise KeyError(job_id)
            if job.get("status") in {"queued", "running"}: raise ValueError("an active import cannot be removed yet")
            data["import_jobs"] = [row for row in data["import_jobs"] if row["id"] != job_id]
            self._save(data)

    def delete_list(self, list_id: str) -> None:
        with self._lock:
            data = self._load()
            before = len(data["lists"])
            data["lists"] = [row for row in data["lists"] if row["id"] != list_id]
            if len(data["lists"]) == before: raise KeyError(list_id)
            self._save(data)

    def get_import_jobs(self) -> list[dict]:
        return self._load()["import_jobs"]

    def get_import_job(self, job_id: str) -> dict | None:
        return next((job for job in self.get_import_jobs() if job["id"] == job_id), None)

    def create_import_job(self, source_input: dict) -> dict:
        with self._lock:
            data = self._load()
            now = datetime.now(timezone.utc).isoformat()
            job = {"id": uuid4().hex, "source_input": source_input, "status": "queued", "progress": 0, "stage": "Queued", "recipe": None, "source": None, "error": "", "created_at": now, "updated_at": now, "events": [{"at": now, "status": "queued", "progress": 0, "stage": "Queued"}]}
            data["import_jobs"].insert(0, job)
            self._save(data)
            return job

    def update_import_job(self, job_id: str, **changes) -> dict:
        allowed = {"status", "progress", "stage", "recipe", "source", "error", "imported_recipe_id"}
        if any(key not in allowed for key in changes): raise ValueError("invalid import job update")
        with self._lock:
            data = self._load()
            job = next((row for row in data["import_jobs"] if row["id"] == job_id), None)
            if job is None: raise KeyError(job_id)
            prior = (job.get("status"), job.get("progress"), job.get("stage"), job.get("error"))
            job.update(changes)
            if job.get("progress") is not None:
                job["progress"] = max(0, min(100, int(job["progress"])))
            job["updated_at"] = datetime.now(timezone.utc).isoformat()
            current = (job.get("status"), job.get("progress"), job.get("stage"), job.get("error"))
            if current != prior:
                job.setdefault("events", []).append({"at": job["updated_at"], "status": job["status"], "progress": job["progress"], "stage": job.get("stage", ""), "error": job.get("error", "")})
                job["events"] = job["events"][-20:]
            self._save(data)
            return job

    def create_generation_job(self, request: dict) -> dict:
        with self._lock:
            data = self._load()
            if any(row.get("status") in {"queued", "running"} for row in data["generation_jobs"]):
                raise ValueError("a healthy-meal generation job is already active")
            now = datetime.now(timezone.utc).isoformat()
            job = {"id": uuid4().hex, "request": request, "status": "queued", "progress": 0, "stage": "Queued", "meals": [], "draft_id": None, "error": "", "created_at": now, "updated_at": now}
            data["generation_jobs"].insert(0, job); self._save(data); return job

    def get_generation_jobs(self) -> list[dict]: return self._load()["generation_jobs"]
    def get_generation_job(self, job_id: str) -> dict | None: return next((row for row in self.get_generation_jobs() if row["id"] == job_id), None)
    def update_generation_job(self, job_id: str, **changes) -> dict:
        with self._lock:
            data = self._load(); job = next((row for row in data["generation_jobs"] if row["id"] == job_id), None)
            if job is None: raise KeyError(job_id)
            job.update({key: value for key, value in changes.items() if key in {"status", "progress", "stage", "meals", "draft_id", "error"}})
            job["progress"] = max(0, min(100, int(job.get("progress", 0)))); job["updated_at"] = datetime.now(timezone.utc).isoformat(); self._save(data); return job

    @transaction
    def cancel_generation_job(self, job_id: str) -> dict:
        job = self.get_generation_job(job_id)
        if not job: raise KeyError(job_id)
        if job.get("status") not in {"queued", "running"}: raise ValueError("generation job is not active")
        return self.update_generation_job(job_id, status="cancelled", progress=100, stage="Stopped by household", error="Stopped by household")

    def delete_generation_job(self, job_id: str) -> None:
        with self._lock:
            data = self._load(); job = next((row for row in data["generation_jobs"] if row["id"] == job_id), None)
            if not job: raise KeyError(job_id)
            data["generation_jobs"] = [row for row in data["generation_jobs"] if row["id"] != job_id]; self._save(data)

    @staticmethod
    def _canonical_source_url(value: str) -> str:
        parsed = urlsplit(str(value).strip())
        query = [(key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True) if not key.lower().startswith(("utm_", "fbclid", "gclid", "igshid"))]
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/") or "/", urlencode(query), ""))

    @staticmethod
    def _meal_key(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(name).lower()).strip()

    def generation_exclusions(self) -> dict[str, set[str]]:
        data = self._load()
        names = {self._meal_key(row.get("name", "")) for row in data["generation_candidates"]}
        urls = {row.get("canonical_source_url", "") for row in data["generation_candidates"]}
        for imported in data["imported_recipes"]:
            names.add(self._meal_key(imported.get("recipe", {}).get("name", "")))
            if imported.get("source", {}).get("url"): urls.add(self._canonical_source_url(imported["source"]["url"]))
        for listing in data["lists"]:
            for meal in listing.get("meals", []):
                names.add(self._meal_key(meal.get("name", "")))
                if meal.get("url"): urls.add(self._canonical_source_url(meal["url"]))
        return {"names": {value for value in names if value}, "urls": {value for value in urls if value}}

    def record_generation_candidates(self, meals: list[dict]) -> None:
        with self._lock:
            data = self._load()
            known = self.generation_exclusions()
            entries = []
            for meal in meals:
                title = self._meal_key(meal.get("name", "")); source = self._canonical_source_url(meal.get("source_url", ""))
                if title in known["names"] or source in known["urls"]:
                    raise ValueError("a generated recipe was already generated or already saved")
                if title in {entry["name_key"] for entry in entries} or source in {entry["canonical_source_url"] for entry in entries}:
                    raise ValueError("generation returned duplicate recipes")
                entries.append({"id": uuid4().hex, "name": meal["name"], "name_key": title, "canonical_source_url": source, "source_title": meal["source_title"], "source_domain": (urlparse(source).hostname or "").lower(), "created_at": datetime.now(timezone.utc).isoformat(), "last_shown_at": datetime.now(timezone.utc).isoformat(), "selected": False, "saved_meal_id": None, "recipe": {key: meal.get(key) for key in ("description", "complexity", "ingredients", "method", "nutrition_note")}})
            data["generation_candidates"].extend(entries)
            self._save(data)

    def get_draft(self, draft_id: str) -> dict | None:
        return next((item for item in self._load()["drafts"] if item["id"] == draft_id), None)

    def get_schedule(self, scheduled_date: str) -> list[dict]:
        try:
            date.fromisoformat(scheduled_date)
        except ValueError:
            raise ValueError("scheduled date must be YYYY-MM-DD")
        return [entry for entry in self._load()["calendar"] if entry["date"] == scheduled_date]

    def schedule_list_meal(self, source_list_id: str, meal_index: int, scheduled_date: str) -> dict:
        try:
            date.fromisoformat(scheduled_date)
        except ValueError:
            raise ValueError("scheduled date must be YYYY-MM-DD")
        with self._lock:
            data = self._load()
            source = next((listing for listing in data["lists"] if listing["id"] == source_list_id), None)
            if source is None: raise KeyError(source_list_id)
            try: meal = source.get("meals", [])[meal_index]
            except (IndexError, TypeError): raise KeyError(meal_index)
            entry = {"id": uuid4().hex, "date": scheduled_date, "meal": meal, "source_list_id": source_list_id, "source_meal_index": meal_index}
            data["calendar"].append(entry)
            self._save(data)
            return entry

    def create_list_from_schedule(self, scheduled_date: str, name: str = "") -> dict:
        try:
            target_date = date.fromisoformat(scheduled_date)
        except ValueError:
            raise ValueError("scheduled date must be YYYY-MM-DD")
        with self._lock:
            data = self._load()
            meals = [entry["meal"] for entry in data["calendar"] if entry["date"] == scheduled_date]
            if not meals: raise ValueError("no meals are planned for this date")
            grouped = {}
            for meal in meals:
                recipe = meal.get("recipe") or meal
                for item in self._recipe_items(recipe.get("ingredients", [])):
                    key = (item["name"].strip().lower(), item.get("unit", "each"), item["aisle"])
                    if key not in grouped:
                        grouped[key] = {**item, "id": f"planned-{uuid4().hex}", "checked": False}
                    else:
                        grouped[key]["quantity"] += item["quantity"]
            listing = {"id": f"{scheduled_date}-{uuid4().hex[:8]}", "name": name.strip() or f"{target_date:%d %B} — planned meals", "date": scheduled_date, "base_servings": 4, "servings": 4, "meals": meals, "items": list(grouped.values())}
            data["lists"].insert(0, listing)
            self._save(data)
            return listing

    def history(self) -> list[dict]:
        data = self._load()
        ratings = data["meal_ratings"]
        meals = []
        for listing in data["lists"]:
            for index, meal in enumerate(listing.get("meals", [])):
                name = meal["name"]
                recipe = meal.get("recipe", {})
                meals.append({"canonical_id": meal.get('canonical_id'), "name": name, "rating": ratings.get(name), "list_id": listing["id"], "meal_index": index, "kind": "list", "url": (f"/api/lists/{listing['id']}/recipes/{index}" if recipe else (meal.get("url") or f"/api/lists/{listing['id']}/recipes/{index}")), "description": recipe.get("summary") or meal.get("description", meal.get("nutrition_note", "")), "complexity": meal.get("complexity", "Easy"), "image_url": recipe.get("image_url", "")})
        return meals

    def positive_rated_meals(self) -> list[dict]:
        """Taste signals for generation; stable name/rating records only."""
        ratings = self._load()["meal_ratings"]
        return [{"name": name, "rating": rating} for name, rating in ratings.items() if isinstance(rating, int) and 3 <= rating <= 5]

    def _mutate(self, list_id: str, callback) -> dict:
        with self._lock:
            data = self._load()
            target = next((item for item in data["lists"] if item["id"] == list_id), None)
            if target is None:
                raise KeyError(list_id)
            callback(target)
            presentation(target)
            self._save(data)
            return target

    def update_servings(self, list_id: str, servings: int) -> dict:
        if not isinstance(servings, int) or servings < 1 or servings > 10:
            raise ValueError("servings must be a whole number from 1 to 10")
        return self._mutate(list_id, lambda target: target.update(servings=servings))

    def toggle_item(self, list_id: str, item_id: str, checked: bool) -> dict:
        def set_checked(target):
            item = next((row for row in target.get("items", []) if row["id"] == item_id), None)
            if item is None:
                raise KeyError(item_id)
            item["checked"] = bool(checked)
        return self._mutate(list_id, set_checked)

    def save_recipe(self, list_id: str, meal_index: int, recipe: dict) -> dict:
        if not isinstance(meal_index, int) or meal_index < 0 or not isinstance(recipe, dict):
            raise ValueError("invalid recipe")
        def save(target):
            try: target["meals"][meal_index]["recipe"] = recipe
            except IndexError: raise KeyError(meal_index)
        return self._mutate(list_id, save)

    def add_item(self, list_id: str, raw_item: dict) -> dict:
        name = str(raw_item.get("name", "")).strip()
        quantity = raw_item.get("quantity", 1)
        if not name or not isinstance(quantity, (int, float)) or quantity <= 0:
            raise ValueError("item name and a positive quantity are required")
        item = normalise_item({**raw_item, "name": name, "quantity": quantity})
        item["id"] = f"manual-{uuid4().hex}"
        item["checked"] = False
        return self._mutate(list_id, lambda target: target.setdefault("items", []).append(item))

    def update_item(self, list_id: str, item_id: str, raw_item: dict) -> dict:
        name = str(raw_item.get("name", "")).strip()
        quantity = raw_item.get("quantity")
        if not name or not isinstance(quantity, (int, float)) or quantity <= 0:
            raise ValueError("item name and a positive quantity are required")
        def edit(target):
            item = next((row for row in target.get("items", []) if row["id"] == item_id), None)
            if item is None: raise KeyError(item_id)
            normalised = normalise_item({**raw_item, "name": name, "quantity": quantity})
            item.update({key: normalised[key] for key in ("name", "quantity", "unit", "notes", "aisle") if key in normalised})
        return self._mutate(list_id, edit)

    def delete_item(self, list_id: str, item_id: str) -> dict:
        def remove(target):
            before = len(target.get("items", []))
            target["items"] = [item for item in target.get("items", []) if item["id"] != item_id]
            if len(target["items"]) == before: raise KeyError(item_id)
        return self._mutate(list_id, remove)

    def delete_list_meal(self, list_id: str, meal_index: int) -> dict:
        if not isinstance(meal_index, int) or meal_index < 0:
            raise ValueError("invalid meal")
        def remove(target):
            try:
                target.setdefault("meals", []).pop(meal_index)
            except IndexError:
                raise KeyError(meal_index)
        return self._mutate(list_id, remove)

    def create_empty_list(self, name: str, list_date: str = "") -> dict:
        name = str(name).strip()
        if not name:
            raise ValueError("a shopping-list name is required")
        if list_date:
            try:
                date.fromisoformat(list_date)
            except ValueError:
                raise ValueError("list date must be YYYY-MM-DD")
        else:
            list_date = date.today().isoformat()
        with self._lock:
            data = self._load()
            listing = {"id": f"{list_date}-{uuid4().hex[:8]}", "name": name, "date": list_date, "base_servings": 4, "servings": 4, "meals": [], "items": []}
            data["lists"].insert(0, listing)
            self._save(data)
            return listing

    @transaction
    def create_draft(self, instruction: str, meals: list[dict]) -> dict:
        data = self._load()
        draft = {"id": uuid4().hex, "instruction": instruction, "meals": meals}
        data["drafts"] = [draft] + data["drafts"][:9]
        self._save(data)
        return draft

    @transaction
    def create_list_from_draft(self, draft_id: str, selected: list[int], name: str = "") -> dict:
        data = self._load()
        draft = next((item for item in data["drafts"] if item["id"] == draft_id), None)
        if not draft:
            raise KeyError(draft_id)
        chosen = [draft["meals"][index] for index in selected if isinstance(index, int) and 0 <= index < len(draft["meals"])]
        if not chosen:
            raise ValueError("select at least one meal")
        grouped = {}
        for meal in chosen:
            for raw in meal["ingredients"]:
                item = normalise_item(raw)
                key = (item["name"].strip().lower(), item.get("unit", "each"), item["aisle"])
                if key not in grouped:
                    grouped[key] = {**item, "id": re.sub(r"[^a-z0-9]+", "-", item["name"].lower()).strip("-") or uuid4().hex, "checked": False}
                else:
                    grouped[key]["quantity"] += item["quantity"]
        list_id = f"{date.today().isoformat()}-{uuid4().hex[:8]}"
        listing = {"id": list_id, "name": name.strip() or f"{date.today():%d %B} — generated meals", "date": date.today().isoformat(), "base_servings": 4, "servings": 4, "meals": chosen, "items": list(grouped.values())}
        data["lists"].insert(0, listing)
        data["drafts"] = [item for item in data["drafts"] if item["id"] != draft_id]
        self._save(data)
        return listing

    @transaction
    def rate_meal(self, name: str, rating: int) -> int:
        if not name or not isinstance(rating, int) or rating < 1 or rating > 5:
            raise ValueError("rating must be a whole number from 1 to 5")
        data = self._load()
        data["meal_ratings"][name] = rating
        self._save(data)
        return rating
