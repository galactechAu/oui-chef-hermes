"""Small JSON-backed store for meal drafts, ratings, and shopping lists."""
import json
import re
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.parse import urlparse
from uuid import uuid4, uuid5, NAMESPACE_URL

from allergies import BASELINE_ALLERGENS, normalise_allergens
from core import normalise_item


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
        known_catalogue = {row.get("recipe_id"): row for row in data["meal_catalog"]}
        for recipe in data["imported_recipes"]:
            if recipe["id"] not in known_catalogue:
                data["meal_catalog"].append({"id": f"meal-{uuid5(NAMESPACE_URL, 'oui-chef:'+recipe['id']).hex}", "recipe_id": recipe["id"]})
        recipe_to_meal = {row["recipe_id"]: row["id"] for row in data["meal_catalog"]}
        data.setdefault("recipe_book_memberships", [])
        for membership in data["recipe_book_memberships"]:
            membership["meal_id"] = recipe_to_meal.get(membership.get("meal_id"), membership.get("meal_id"))
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2) + "\n")
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
        data = self._load(); recipes = {row["id"]: row for row in data["imported_recipes"]}
        return [{**row, "recipe": recipes[row["recipe_id"]].get("recipe", {}), "source": recipes[row["recipe_id"]].get("source", {})} for row in data["meal_catalog"] if row.get("recipe_id") in recipes]

    def get_imported_recipes(self) -> list[dict]:
        return self._load()["imported_recipes"]

    def get_imported_recipe(self, recipe_id: str) -> dict | None:
        return next((item for item in self.get_imported_recipes() if item["id"] == recipe_id), None)

    def _present_recipe_book(self, data: dict, book: dict) -> dict:
        ids = [row["meal_id"] for row in data["recipe_book_memberships"] if row["book_id"] == book["id"]]
        catalogue = {row["id"]: row for row in self.get_meal_catalogue()}
        meals = [catalogue[meal_id] for meal_id in ids if meal_id in catalogue]
        return {**book, "meal_count": len(meals), "meal_ids": ids, "meal_preview": [row["recipe"]["name"] for row in meals[:3]], "meals": meals}

    def get_recipe_books(self, query: str = "") -> list[dict]:
        data = self._load(); needle = str(query).casefold().strip(); books = [self._present_recipe_book(data, row) for row in data["recipe_books"]]
        return [row for row in books if not needle or needle in row["title"].casefold() or any(needle in name.casefold() for name in row["meal_preview"])]

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
            book["title"] = clean; self._save(data); return self._present_recipe_book(data, book)

    def create_list_from_recipe_book(self, book_id: str, meal_ids: list[str], name: str = "", list_id: str = "") -> dict:
        book = self.get_recipe_book(book_id)
        if not book: raise KeyError(book_id)
        selected = meal_ids or book["meal_ids"]
        if not selected or any(value not in set(book["meal_ids"]) for value in selected): raise ValueError("select one or more meals from this Recipe Book")
        catalogue = {row["id"]: row for row in self.get_meal_catalogue()}
        recipes = [catalogue[value] for value in selected]
        if list_id:
            listing = None
            for meal in recipes:
                listing = self._add_recipe_to_list(meal["recipe"], meal.get("source", {}), list_id, name)
            return listing
        return self.create_list_from_imported_recipes([meal["recipe_id"] for meal in recipes], name)

    def delete_recipe_book(self, book_id: str) -> None:
        with self._lock:
            data = self._load(); before = len(data["recipe_books"]); data["recipe_books"] = [row for row in data["recipe_books"] if row["id"] != book_id]
            if len(data["recipe_books"]) == before: raise KeyError(book_id)
            data["recipe_book_memberships"] = [row for row in data["recipe_book_memberships"] if row["book_id"] != book_id]; self._save(data)

    def add_meals_to_recipe_book(self, book_id: str, meal_ids: list[str]) -> dict:
        with self._lock:
            data = self._load(); book = next((row for row in data["recipe_books"] if row["id"] == book_id), None)
            selected = list(dict.fromkeys(str(value) for value in meal_ids))
            recipe_to_catalogue = {row["recipe_id"]: row["id"] for row in data["meal_catalog"]}
            selected = [recipe_to_catalogue.get(value, value) for value in selected]
            if not book: raise KeyError(book_id)
            if not selected or any(not any(meal["id"] == value for meal in data["meal_catalog"]) for value in selected): raise ValueError("select one or more saved Meals")
            known = {(row["book_id"], row["meal_id"]) for row in data["recipe_book_memberships"]}
            for meal_id in selected:
                if (book_id, meal_id) not in known: data["recipe_book_memberships"].append({"book_id": book_id, "meal_id": meal_id})
            self._save(data); return self._present_recipe_book(data, book)

    def remove_meal_from_recipe_book(self, book_id: str, meal_id: str) -> dict:
        with self._lock:
            data = self._load(); book = next((row for row in data["recipe_books"] if row["id"] == book_id), None)
            if not book: raise KeyError(book_id)
            data["recipe_book_memberships"] = [row for row in data["recipe_book_memberships"] if not (row["book_id"] == book_id and row["meal_id"] == meal_id)]
            self._save(data); return self._present_recipe_book(data, book)

    def get_uncategorised_meals(self) -> list[dict]:
        data = self._load(); member_ids = {row["meal_id"] for row in data["recipe_book_memberships"]}; return [row for row in self.get_meal_catalogue() if row["id"] not in member_ids]

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
                match = re.match(r"^(?P<quantity>\d+(?:\.\d+)?(?:/\d+)?)\s*(?P<unit>kg|g|ml|l|tbsp|tsp|cups?|cans?|packs?|cloves?|each|pieces?)?\s*(?P<name>.*)$", text, re.I)
                quantity, unit, name = 1, "each", text
                if match and match.group("name"):
                    value = match.group("quantity")
                    quantity = float(value.split("/")[0]) / float(value.split("/")[1]) if "/" in value else float(value)
                    unit, name = (match.group("unit") or "each").lower(), match.group("name").strip(" ,-")
                    if unit.rstrip("s") in {"clove", "piece"}: unit = "each"
                raw = {"name": name, "quantity": quantity, "unit": unit, "notes": text if text != name else ""}
            item = normalise_item(raw)
            item["id"] = f"recipe-{uuid4().hex}"
            item["checked"] = False
            rows.append(item)
        return rows

    def _add_recipe_to_list(self, recipe: dict, source: dict, list_id: str = "", name: str = "") -> dict:
        if not recipe.get("name") or not isinstance(recipe.get("ingredients"), list): raise ValueError("this meal does not have recipe ingredients yet")
        meal = {"name": recipe["name"], "recipe": recipe, "url": source.get("url", "")}
        items = self._recipe_items(recipe["ingredients"])
        if list_id:
            return self._mutate(list_id, lambda target: (target.setdefault("meals", []).append(meal), target.setdefault("items", []).extend(items)))
        data = self._load()
        listing = {"id": f"{date.today().isoformat()}-{uuid4().hex[:8]}", "name": name.strip() or f"{recipe['name']} — shopping list", "date": date.today().isoformat(), "base_servings": 4, "servings": 4, "meals": [meal], "items": items}
        data["lists"].insert(0, listing)
        self._save(data)
        return listing

    def add_imported_recipe_to_list(self, recipe_id: str, list_id: str = "", name: str = "") -> dict:
        imported = self.get_imported_recipe(recipe_id)
        if not imported: raise KeyError(recipe_id)
        return self._add_recipe_to_list(imported["recipe"], imported.get("source", {}), list_id, name)

    def add_list_meal_to_list(self, source_list_id: str, meal_index: int, list_id: str = "", name: str = "") -> dict:
        source_list = self.get_list(source_list_id)
        if not source_list: raise KeyError(source_list_id)
        try: meal = source_list["meals"][meal_index]
        except (IndexError, TypeError): raise KeyError(meal_index)
        recipe = meal.get("recipe") or {"name": meal.get("name", ""), "ingredients": meal.get("ingredients", [])}
        return self._add_recipe_to_list(recipe, {"url": meal.get("url", "")}, list_id, name)

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

    def create_list_from_imported_recipes(self, recipe_ids: list[str], name: str = "") -> dict:
        selected = [row for row in self.get_imported_recipes() if row["id"] in set(recipe_ids)]
        if not selected or len(selected) != len(set(recipe_ids)): raise ValueError("select one or more saved Meals")
        data = self._load(); meals = [{"name": row["recipe"]["name"], "recipe": row["recipe"], "url": row.get("source", {}).get("url", "")} for row in selected]
        items = [item for row in selected for item in self._recipe_items(row["recipe"]["ingredients"])]
        listing = {"id": f"{date.today().isoformat()}-{uuid4().hex[:8]}", "name": name.strip() or "Selected meals — shopping list", "date": date.today().isoformat(), "base_servings": 4, "servings": 4, "meals": meals, "items": items}
        data["lists"].insert(0, listing); self._save(data); return listing

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
                meals.append({"name": name, "rating": ratings.get(name), "list_id": listing["id"], "meal_index": index, "kind": "list", "url": (f"/api/lists/{listing['id']}/recipes/{index}" if recipe else (meal.get("url") or f"/api/lists/{listing['id']}/recipes/{index}")), "description": recipe.get("summary") or meal.get("description", meal.get("nutrition_note", "")), "complexity": meal.get("complexity", "Easy"), "image_url": recipe.get("image_url", "")})
        return meals

    def positive_rated_meals(self) -> list[dict]:
        """Taste signals for generation; stable name/rating records only."""
        ratings = self._load()["meal_ratings"]
        return [{"name": name, "rating": rating} for name, rating in ratings.items() if isinstance(rating, int) and 3 <= rating <= 5]

    def _mutate(self, list_id: str, callback) -> dict:
        data = self._load()
        target = next((item for item in data["lists"] if item["id"] == list_id), None)
        if target is None:
            raise KeyError(list_id)
        callback(target)
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

    def create_draft(self, instruction: str, meals: list[dict]) -> dict:
        data = self._load()
        draft = {"id": uuid4().hex, "instruction": instruction, "meals": meals}
        data["drafts"] = [draft] + data["drafts"][:9]
        self._save(data)
        return draft

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

    def rate_meal(self, name: str, rating: int) -> int:
        if not name or not isinstance(rating, int) or rating < 1 or rating > 5:
            raise ValueError("rating must be a whole number from 1 to 5")
        data = self._load()
        data["meal_ratings"][name] = rating
        self._save(data)
        return rating
