#!/usr/bin/env python3
"""Oui, Chef: shopping lists plus natural-language Hermes meal suggestions."""
import html
import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from urllib.error import URLError
from urllib.request import Request, urlopen
from queue import Empty

from core import scaled_purchase_quantity, presentation
from allergies import allergy_error, detect_allergens
from generation import extract_json, generation_prompt, validate_generation, verify_source_evidence
from recipe_importer import classify_source, decode_image_data, fetch_public_source_text, recipe_import_prompt, validate_public_url, validate_public_redirects
from recipe_page import render_recipe_page
from realtime import EventHub
from store import ShoppingStore

ROOT = Path(__file__).parent
HUB = EventHub()
IMPORT_LOCK = threading.Lock()
STORE = ShoppingStore(ROOT / "data" / "lists.json", publish=HUB.publish)
PORT = int(os.environ.get("PORT", "8094"))
BRIDGE_URL = os.environ.get("HERMES_BRIDGE_URL", "").strip() or None


def health_payload() -> dict:
    return {"status": "ok", "service": "oui-chef", "hermes_bridge_configured": bool(BRIDGE_URL)}



def paginate_search(rows: list[dict], query: str = "", page: int = 1, page_size: int = 12) -> dict:
    """Return a stable, bounded API page for user-visible recipe/job libraries."""
    page = max(1, int(page)); page_size = min(100, max(1, int(page_size)))
    query = str(query).casefold().strip()
    filtered = [row for row in rows if not query or query in str(row.get("name", "")).casefold() or query in str(row.get("description", "")).casefold()]
    total = len(filtered); total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    start = (page - 1) * page_size
    return {"items": filtered[start:start + page_size], "total": total, "page": page, "page_size": page_size, "total_pages": total_pages}


def request_hermes(prompt: str, timeout: int) -> dict:
    if not BRIDGE_URL:
        raise ValueError("Hermes generation is not configured. Set HERMES_BRIDGE_URL to a private /generate bridge, then retry.")
    request = Request(BRIDGE_URL, data=json.dumps({"prompt": prompt}).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = json.loads(response.read())
    except (URLError, TimeoutError, OSError) as error:
        raise ValueError("Hermes generation bridge is unavailable. Check HERMES_BRIDGE_URL and keep the bridge private.") from error
    if not raw.get("ok"):
        raise ValueError(raw.get("error", "Hermes generation failed"))
    return raw


def ask_hermes(count: int, instruction: str, complexity: str, progress=None) -> list[dict]:
    """Generate a source-backed batch; source discovery can require several minutes."""
    progress = progress or (lambda *_args: None)
    progress(15, "Finding public recipe sources")
    exclusions = STORE.generation_exclusions()
    allergies = STORE.get_dietary_allergies()
    prompt = generation_prompt(count, instruction, complexity, STORE.history(), exclusions, STORE.positive_rated_meals(), allergies)
    meals = validate_generation(extract_json(request_hermes(prompt, 600)["output"]), allergies)
    progress(70, "Verifying cited public sources")
    for meal in meals:
        meal["source_url"] = validate_public_redirects(meal["source_url"])
        if not verify_source_evidence(meal, fetch_public_source_text(meal["source_url"])):
            raise ValueError(f"source evidence could not verify {meal['name']}; generation returned insufficient unique public sources")
    STORE.record_generation_candidates(meals)
    return meals


def import_recipe_prompt(url: str) -> str:
    return f'''Open and inspect this recipe URL: {url}. Extract its published recipe into a cooking-ready compact JSON object only. Use browser/web tools if needed. Do not invent content. Return {{"summary":"", "image_url":"direct public image URL or empty", "complexity":"Easy|Moderate|Advanced", "prep_time_min":integer or null, "cook_time_min":integer or null, "health_rating":integer 1-5, "ingredients":["short item"], "steps":["short imperative step"]}}. Preserve every preparation action needed to execute the recipe: state how to chop, dice, slice, mince, grate, trim, drain, measure, or otherwise prepare each ingredient before it is used. Put each preparation action in its own first-use step or explicitly include it before the ingredient is used; never assume the cook inferred it from the ingredient list. Steps must be standalone, actionable instructions with heat, timing, quantities and doneness cues when the source provides them. Use the source's stated prep/cook times; return null when absent. Health rating is Meal Planner's fat-loss fit: 5=high-protein, lower-carb, low-added-sugar and fat-conscious; 1=poor fit. Keep at most 12 ingredients and 15 steps. No markdown.'''



def ask_hermes_recipe(url: str) -> dict:
    if not url.startswith(("https://", "http://")):
        raise ValueError("this meal has no external recipe link to import")
    raw = request_hermes(import_recipe_prompt(url), 300)
    recipe = extract_json(raw["output"])
    if not isinstance(recipe, dict) or not isinstance(recipe.get("steps"), list): raise ValueError("recipe source did not return usable steps")
    recipe.setdefault("summary", ""); recipe.setdefault("image_url", ""); recipe.setdefault("ingredients", [])
    if recipe.get("complexity") not in {"Easy", "Moderate", "Advanced"}: recipe.pop("complexity", None)
    for key in ("prep_time_min", "cook_time_min"):
        if not isinstance(recipe.get(key), int) or isinstance(recipe.get(key), bool) or not 0 < recipe[key] <= 480: recipe.pop(key, None)
    if not isinstance(recipe.get("health_rating"), int) or isinstance(recipe.get("health_rating"), bool) or not 1 <= recipe["health_rating"] <= 5: recipe.pop("health_rating", None)
    return recipe


def image_ocr_text(image_data: str) -> str:
    mime, raw = decode_image_data(image_data)
    suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}[mime]
    with tempfile.NamedTemporaryFile(suffix=suffix) as image:
        image.write(raw); image.flush()
        result = subprocess.run(["tesseract", image.name, "stdout", "-l", "eng"], capture_output=True, text=True, timeout=45)
    if result.returncode != 0 or len(result.stdout.strip()) < 12:
        raise ValueError("could not read enough recipe text from that image; try a clearer screenshot or paste the text")
    return result.stdout.strip()


def public_video_transcript(url: str, progress=None) -> str:
    """Download only public media, then transcribe its audio with local Whisper."""
    progress = progress or (lambda *_args: None)
    with tempfile.TemporaryDirectory(prefix="meal-import-") as directory:
        folder = Path(directory)
        source_template = str(folder / "source.%(ext)s")
        progress(None, "Downloading public video audio")
        download = subprocess.run(["yt-dlp", "--no-playlist", "--no-warnings", "--socket-timeout", "25", "--retries", "1", "--max-filesize", "80M", "-f", "bestaudio/best", "-o", source_template, url], capture_output=True, text=True, timeout=150)
        media = next(folder.glob("source.*"), None)
        if download.returncode != 0 or media is None:
            raise ValueError("the public video audio could not be downloaded; Instagram may require login or may have restricted this Reel")
        audio = folder / "audio.wav"
        progress(None, "Preparing audio for transcription")
        converted = subprocess.run(["ffmpeg", "-y", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000", str(audio)], capture_output=True, text=True, timeout=120)
        if converted.returncode != 0:
            raise ValueError("the public video audio could not be prepared for transcription")
        output_base = str(folder / "transcript")
        progress(None, "Transcribing spoken recipe")
        transcribed = subprocess.run(["whisper-cli", "-m", "/models/ggml-tiny.en.bin", "-f", str(audio), "-nt", "-otxt", "-of", output_base], capture_output=True, text=True, timeout=360)
        transcript_file = Path(output_base + ".txt")
        transcript = transcript_file.read_text().strip() if transcript_file.exists() else ""
        if transcribed.returncode != 0 or len(transcript) < 20:
            raise ValueError("the Reel audio could not be transcribed clearly enough to build a recipe")
        return transcript


def ask_import_recipe(body: dict, progress=None) -> tuple[dict, dict]:
    progress = progress or (lambda *_args: None)
    progress(10, "Checking source")
    url = str(body.get("url", "")).strip()
    text = str(body.get("text", "")).strip()
    image_data = str(body.get("image_data", "")).strip()
    source_type = classify_source(url=url, text=text, image_data=image_data)
    if source_type in {"webpage", "youtube", "instagram"}:
        content = validate_public_url(url)
        source = {"type": source_type, "url": content, "label": content}
    elif source_type == "image":
        progress(25, "Reading recipe image")
        content = image_ocr_text(image_data)
        source = {"type": "image", "label": "Uploaded recipe image"}
    else:
        if len(text) > 12000: raise ValueError("pasted recipe text is limited to 12,000 characters")
        content = text
        source = {"type": "text", "label": "Pasted recipe text"}
    progress(None, "Analysing published recipe — this can take a few minutes")
    raw = request_hermes(recipe_import_prompt(source_type, content), 300)
    recipe = extract_json(raw["output"])
    incomplete_video_recipe = source_type in {"youtube", "instagram"} and (not isinstance(recipe, dict) or recipe.get("error") or not recipe.get("ingredients") or not recipe.get("steps"))
    if incomplete_video_recipe:
        transcript = public_video_transcript(content, progress)
        progress(None, "Analysing transcript")
        raw = request_hermes(recipe_import_prompt("text", "Public video speech transcript:\n" + transcript), 300)
        recipe = extract_json(raw["output"])
        source["transcript_used"] = True
        source["label"] = f"{content} (public audio transcript)"
    progress(85, "Checking ingredients and method")
    if not isinstance(recipe, dict): raise ValueError("the source did not yield a usable recipe")
    if recipe.get("error"): raise ValueError(str(recipe["error"]))
    name = str(recipe.get("name", "")).strip()
    ingredients, steps = recipe.get("ingredients"), recipe.get("steps")
    if not name or not isinstance(ingredients, list) or not ingredients or not isinstance(steps, list) or not steps:
        raise ValueError("the source did not provide a complete recipe with ingredients and steps")
    combined = " ".join([name, str(recipe.get("summary", "")), str(recipe.get("notes", ""))] + list(map(str, ingredients + steps)))
    matches = detect_allergens(combined, STORE.get_dietary_allergies())
    if matches: raise allergy_error(matches)
    recipe["name"] = name; recipe["ingredients"] = [str(x) for x in ingredients[:15]]; recipe["steps"] = [str(x) for x in steps[:18]]
    return recipe, source


def run_import_job(job_id: str) -> None:
    try:
        STORE.update_import_job(job_id, status="running", progress=5, stage="Starting")
        with IMPORT_LOCK:
            job = STORE.get_import_job(job_id)
            recipe, source = ask_import_recipe(job["source_input"], lambda progress, stage: STORE.update_import_job(job_id, status="running", progress=progress, stage=stage))
            STORE.update_import_job(job_id, status="review", progress=100, stage="Ready to review", recipe=recipe, source=source)
    except Exception as error:
        STORE.update_import_job(job_id, status="failed", progress=100, stage="Import failed", error=str(error))


def start_import_job(source_input: dict) -> dict:
    job = STORE.create_import_job(source_input)
    threading.Thread(target=run_import_job, args=(job["id"],), daemon=True, name=f"recipe-import-{job['id'][:8]}").start()
    return job


def update_generation_progress(job_id: str, progress: int, stage: str) -> None:
    job = STORE.get_generation_job(job_id)
    if job and job.get("status") in {"queued", "running"}:
        STORE.update_generation_job(job_id, status="running", progress=progress, stage=stage)


def run_generation_job(job_id: str) -> None:
    try:
        with IMPORT_LOCK:
            STORE.update_generation_job(job_id, status="running", progress=5, stage="Starting")
            job = STORE.get_generation_job(job_id)
            if not job or job.get("status") == "cancelled": return
            request = job["request"]
            meals = ask_hermes(request["count"], request["instruction"], request["complexity"], lambda progress, stage: update_generation_progress(job_id, progress, stage))
            if not STORE.get_generation_job(job_id) or STORE.get_generation_job(job_id).get("status") == "cancelled": return
            draft = STORE.create_draft(request["instruction"], meals)
            STORE.update_generation_job(job_id, status="review", progress=100, stage="Ready to review", meals=meals, draft_id=draft["id"])
    except Exception as error:
        if STORE.get_generation_job(job_id):
            STORE.update_generation_job(job_id, status="failed", progress=100, stage="Generation failed", error=str(error))

def start_generation_job(request: dict) -> dict:
    job = STORE.create_generation_job(request)
    threading.Thread(target=run_generation_job, args=(job["id"],), daemon=True, name=f"meal-generation-{job['id'][:8]}").start()
    return job


def resume_generation_jobs() -> None:
    for job in STORE.get_generation_jobs():
        if job.get("status") in {"queued", "running"}:
            STORE.update_generation_job(job["id"], status="queued", progress=0, stage="Queued after restart")
            threading.Thread(target=run_generation_job, args=(job["id"],), daemon=True, name=f"meal-generation-{job['id'][:8]}").start()


class Handler(BaseHTTPRequestHandler):
    server_version = "OuiChef/2.0"
    def log_message(self, *_args): pass

    def send_json(self, status: int, data):
        raw = json.dumps(data).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(raw)

    def body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 <= length <= 16 * 1024 * 1024: raise ValueError('request body is too large')
            body = json.loads(self.rfile.read(length)) if length else {}
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError('a valid JSON object is required') from error
        if not isinstance(body, dict): raise ValueError('a JSON object is required')
        return body

    def send_events(self):
        subscriber = HUB.subscribe()
        try:
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-cache"); self.send_header("Connection", "keep-alive"); self.end_headers()
            self.wfile.write(b"event: connected\ndata: {}\n\n"); self.wfile.flush()
            while True:
                try:
                    event = subscriber.get(timeout=15)
                    frame = f"id: {event['id']}\nevent: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()
                except Empty:
                    frame = b": heartbeat\n\n"
                self.wfile.write(frame); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            HUB.unsubscribe(subscriber)

    def do_GET(self):
        path = urlparse(self.path).path
        pages = {"/": "index.html", "/index.html": "index.html"}
        if path in pages:
            raw = (ROOT / "static" / pages[path]).read_bytes(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); return self.wfile.write(raw)
        if path == "/api/settings/dietary-allergies": return self.send_json(200, {"allergies": STORE.get_dietary_allergies()})
        if path in {"/api/recipe-books", "/api/recipe-books/uncategorised", "/api/recipe-books/recent"}:
            from urllib.parse import parse_qs
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            try:
                if path.endswith('/recent'):
                    limit = int(query.get('limit', ['5'])[0])
                    if not 1 <= limit <= 100: raise ValueError('limit must be from 1 to 100')
                    return self.send_json(200, {'books': STORE.get_recent_recipe_books(limit)})
                page = int(query.get('page', ['1'])[0])
                size = int(query.get('page_size', ['12'])[0])
                if page < 1 or not 1 <= size <= 100: raise ValueError('page must be positive and page_size from 1 to 100')
                search = query.get('q', [''])[0]
                uncategorised = path.endswith('/uncategorised')
                rows = STORE.get_uncategorised_meals() if uncategorised else STORE.get_recipe_books(search)
                result = paginate_search(rows, search if uncategorised else '', page, size)
                return self.send_json(200, {**result, 'meals' if uncategorised else 'books': result['items']})
            except ValueError as error:
                return self.send_json(400, {'error': str(error)})
        if path.startswith("/api/recipe-books/"):
            book = STORE.get_recipe_book(path.rsplit("/", 1)[-1]); return self.send_json(200, book) if book else self.send_json(404, {"error": "recipe book not found"})
        if path == "/api/events": return self.send_events()
        if path == "/health": return self.send_json(200, health_payload())
        if path == "/api/calendar":
            query = urlparse(self.path).query
            from urllib.parse import parse_qs
            scheduled_date = parse_qs(query).get("date", [""])[0]
            return self.send_json(200, {"entries": STORE.get_schedule(scheduled_date) if scheduled_date else STORE._load()["calendar"]})
        if path == "/api/generation-jobs": return self.send_json(200, {"jobs": STORE.get_generation_jobs()})
        if path.startswith("/api/generation-jobs/"):
            job = STORE.get_generation_job(path.rsplit("/", 1)[-1]); return self.send_json(200, job) if job else self.send_json(404, {"error": "generation job not found"})
        if path == "/api/import-jobs":
            from urllib.parse import parse_qs
            query = parse_qs(urlparse(self.path).query); search = query.get("q", query.get("search", [""]))[0]; page = query.get("page", [1])[0]; page_size = query.get("page_size", query.get("limit", [12]))[0]
            jobs = [{**job, "name": (job.get("recipe") or {}).get("name", str(job.get("source_input", "")))} for job in STORE.get_import_jobs()]
            result = paginate_search(jobs, search, page, page_size); return self.send_json(200, {"jobs": result["items"], **result, "limit": result["page_size"]})
        if path.startswith("/api/import-jobs/"):
            job = STORE.get_import_job(path.rsplit("/", 1)[-1])
            return self.send_json(200, job) if job else self.send_json(404, {"error": "import job not found"})
        if path == "/api/import-recipes": return self.send_json(200, {"recipes": STORE.get_imported_recipes()})
        if path.startswith("/api/import-recipes/"):
            imported = STORE.get_imported_recipe(path.rsplit("/", 1)[-1])
            if not imported: return self.send_json(404, {"error": "imported recipe not found"})
            meal = {"name": imported["recipe"]["name"], "recipe": imported["recipe"], "url": imported.get("source", {}).get("url", "")}
            raw = render_recipe_page(meal)
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); return self.wfile.write(raw)
        if path == "/api/aisles/order": return self.send_json(200, {"order": STORE.get_aisle_order()})
        if path == "/api/lists": return self.send_json(200, {"lists": [{k:v for k,v in row.items() if k not in {"items", "meals"}} for row in STORE.get_lists()]})
        if path == "/api/meals":
            from urllib.parse import parse_qs
            query = parse_qs(urlparse(self.path).query); search = query.get("q", query.get("search", [""]))[0]; page = query.get("page", [1])[0]; page_size = query.get("page_size", query.get("limit", [12]))[0]
            catalogue = STORE.get_meal_catalogue()
            meals = [{**row, 'id': row.get('recipe_id', row['id']), 'complexity': row['recipe'].get('complexity', 'Easy')} for row in catalogue]
            try:
                result = paginate_search(meals, search, page, page_size)
                return self.send_json(200, {"meals": result["items"], **result, "limit": result["page_size"]})
            except ValueError as error:
                return self.send_json(400, {'error': str(error)})
        if path == "/api/history": return self.send_json(200, {"meals": STORE.history()})
        if path.startswith("/api/lists/") and "/recipes/" in path:
            parts = path.split("/")
            try:
                meal = STORE.get_list(parts[3])["meals"][int(parts[5])]
                raw = render_recipe_page(meal)
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); return self.wfile.write(raw)
            except (KeyError, IndexError, ValueError, TypeError): return self.send_json(404, {"error": "recipe not found"})
        if path.startswith("/api/lists/"):
            target = STORE.get_list(path.rsplit("/", 1)[-1]); return self.send_json(200, presentation(target)) if target else self.send_json(404, {"error":"list not found"})
        return self.send_json(404, {"error":"not found"})

    def do_DELETE(self):
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/recipe-books/") and "/meals/" in path:
                parts = path.split("/"); return self.send_json(200, STORE.remove_meal_from_recipe_book(parts[3], parts[5]))
            if path.startswith("/api/recipe-books/"):
                STORE.delete_recipe_book(path.rsplit("/", 1)[-1]); return self.send_json(200, {"deleted": True})
            if path.startswith("/api/generation-jobs/") and "/meals/" in path:
                parts = path.split("/"); return self.send_json(200, STORE.dismiss_generation_job_meal(parts[3], int(parts[5])))
            if path.startswith("/api/generation-jobs/"):
                STORE.delete_generation_job(path.rsplit("/", 1)[-1]); return self.send_json(200, {"deleted": True})
            if path.startswith("/api/import-jobs/"):
                STORE.delete_import_job(path.rsplit("/", 1)[-1]); return self.send_json(200, {"deleted": True})
            if path.startswith("/api/import-recipes/"):
                STORE.delete_imported_recipe(path.rsplit("/", 1)[-1]); return self.send_json(200, {"deleted": True})
            if path.startswith("/api/lists/") and "/meals/" in path:
                parts = path.split("/"); return self.send_json(200, presentation(STORE.delete_list_meal(parts[3], int(parts[5]))) )
            if path.startswith("/api/lists/") and "/items/" in path:
                parts = path.split("/"); STORE.delete_item(parts[3], parts[5]); return self.send_json(200, {"deleted": True})
            if path.startswith("/api/lists/"):
                STORE.delete_list(path.rsplit("/", 1)[-1]); return self.send_json(200, {"deleted": True})
        except (KeyError, ValueError) as error: return self.send_json(400, {"error": str(error)})
        return self.send_json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self.body()
            if path.startswith('/api/recipe-books'):
                for field in ('title', 'name', 'list_id'):
                    if field in body and not isinstance(body[field], str): raise ValueError(f'{field} must be a string')
            if path == '/api/recipe-books/memberships':
                return self.send_json(200, {'books': STORE.add_meals_to_recipe_books(body.get('book_ids'), body.get('meal_ids'))})
            if path.startswith('/api/recipe-books/') and path.endswith('/pin'):
                return self.send_json(200, STORE.pin_recipe_book(path.split('/')[3], body.get('pinned')))
            if path.startswith('/api/recipe-books/') and path.endswith('/use'):
                return self.send_json(200, STORE.use_recipe_book(path.split('/')[3]))
            if path == "/api/settings/dietary-allergies": return self.send_json(200, {"allergies": STORE.set_dietary_allergies(body.get("allergies", []))})
            if path == "/api/recipe-books": return self.send_json(201, STORE.create_recipe_book(str(body.get("title", ""))))
            if path.startswith("/api/recipe-books/") and path.endswith("/create-list"):
                if 'all' in body and not isinstance(body['all'], bool): raise ValueError('all must be a boolean')
                if 'meal_ids' in body: STORE._ids(body['meal_ids'])
                if 'servings' in body and (type(body['servings']) is not int or not 1 <= body['servings'] <= 10):
                    raise ValueError('servings must be a whole number from 1 to 10')
                selected = None if body.get('all') else body.get('meal_ids')
                return self.send_json(201, presentation(STORE.create_list_from_recipe_book(path.split('/')[3], selected, body.get('name', ''), body.get('list_id', ''), body.get('servings'))))
            if path.startswith("/api/recipe-books/") and path.endswith("/meals"):
                return self.send_json(200, STORE.add_meals_to_recipe_book(path.split("/")[3], body.get("meal_ids", [])))
            if path.startswith("/api/recipe-books/") and path.endswith("/rename"):
                return self.send_json(200, STORE.rename_recipe_book(path.split("/")[3], str(body.get("title", ""))))
            if path == "/api/aisles/order": return self.send_json(200, {"order": STORE.set_aisle_order(body.get("order", []))})
            if path.startswith("/api/import-recipes/") and path.endswith("/add-to-list"):
                return self.send_json(201, presentation(STORE.add_imported_recipe_to_list(path.split("/")[3], str(body.get("list_id", "")), str(body.get("name", "")))))
            if path.startswith("/api/lists/") and path.endswith("/add-to-list") and "/recipes/" in path:
                parts = path.split("/")
                return self.send_json(201, presentation(STORE.add_list_meal_to_list(parts[3], int(parts[5]), str(body.get("list_id", "")), str(body.get("name", "")))))
            if path == "/api/import-jobs": return self.send_json(202, start_import_job(body))
            if path.startswith("/api/import-jobs/") and path.endswith("/retry"):
                job = STORE.get_import_job(path.split("/")[3])
                if not job: raise ValueError("import job not found")
                return self.send_json(202, start_import_job(job["source_input"]))
            if path.startswith("/api/import-jobs/") and path.endswith("/save"):
                job = STORE.get_import_job(path.split("/")[3])
                if not job or job.get("status") not in {"review", "saved"}: raise ValueError("this import is not ready for review")
                if job.get("imported_recipe_id"): return self.send_json(200, job)
                saved = STORE.save_imported_recipe(job["recipe"], job["source"])
                return self.send_json(201, STORE.update_import_job(job["id"], status="saved", progress=100, stage="Saved to Meals", imported_recipe_id=saved["id"]))
            if path == "/api/import-recipes/preview":
                recipe, source = ask_import_recipe(body)
                return self.send_json(200, {"recipe": recipe, "source": source})
            if path == "/api/import-recipes":
                recipe, source = body.get("recipe"), body.get("source")
                if not isinstance(source, dict): raise ValueError("a reviewed recipe source is required")
                return self.send_json(201, STORE.save_imported_recipe(recipe, source))
            if path == "/api/lists":
                return self.send_json(201, presentation(STORE.create_empty_list(str(body.get("name", "")), str(body.get("date", "")))))
            if path == "/api/calendar":
                return self.send_json(201, STORE.schedule_list_meal(str(body.get("list_id", "")), int(body.get("meal_index", -1)), str(body.get("date", ""))))
            if path.startswith("/api/calendar/") and path.endswith("/create-list"):
                scheduled_date = path.split("/")[3]
                return self.send_json(201, presentation(STORE.create_list_from_schedule(scheduled_date, str(body.get("name", "")))))
            if path.startswith("/api/generation-jobs/") and path.endswith("/save-meals"):
                return self.send_json(201, {"recipes": STORE.save_generation_job_meals(path.split("/")[3], body.get("selected", []))})
            if path == "/api/meals/create-list":
                if 'meal_ids' in body:
                    for field in ('name', 'list_id'):
                        if field in body and not isinstance(body[field], str): raise ValueError(f'{field} must be a string')
                    return self.send_json(201, presentation(STORE.create_list_from_meals(body['meal_ids'], body.get('name', ''), body.get('list_id', ''), body.get('servings'))))
                return self.send_json(201, presentation(STORE.create_list_from_imported_recipes(body.get("recipe_ids", []), str(body.get("name", "")))))
            if path.startswith("/api/generation-jobs/") and path.endswith("/stop"):
                return self.send_json(200, STORE.cancel_generation_job(path.split("/")[3]))
            if path == "/api/generate":
                count = int(body.get("count", 6)); instruction = str(body.get("instruction", "")).strip(); complexity = str(body.get("complexity", "Easy"))
                if not 1 <= count <= 12: raise ValueError("choose between 1 and 12 meals")
                if len(instruction) > 2000: raise ValueError("instructions are limited to 2,000 characters")
                return self.send_json(202, start_generation_job({"count": count, "instruction": instruction, "complexity": complexity}))
            if path.startswith("/api/drafts/") and path.endswith("/create"):
                draft_id = path.split("/")[3]; return self.send_json(201, presentation(STORE.create_list_from_draft(draft_id, body.get("selected", []), str(body.get("name", "")))))
            if path == "/api/meals/rate": return self.send_json(200, {"rating": STORE.rate_meal(str(body.get("name", "")), int(body.get("rating", 0)))})
            if path.startswith("/api/lists/") and path.endswith("/scrape") and "/recipes/" in path:
                parts = path.split("/"); listing = STORE.get_list(parts[3]); meal = listing["meals"][int(parts[5])]
                return self.send_json(200, presentation(STORE.save_recipe(parts[3], int(parts[5]), ask_hermes_recipe(meal.get("url", "")))))
            if path.endswith("/servings") and path.startswith("/api/lists/"):
                return self.send_json(200, presentation(STORE.update_servings(path.split("/")[3], int(body.get("servings", 0)))))
            if path.startswith("/api/lists/") and "/items/" in path:
                parts = path.split("/"); return self.send_json(200, presentation(STORE.update_item(parts[3], parts[5], body)))
            if path.endswith("/items") and path.startswith("/api/lists/"):
                return self.send_json(201, presentation(STORE.add_item(path.split("/")[3], body)))
            if path.endswith("/check") and path.startswith("/api/lists/"):
                return self.send_json(200, presentation(STORE.toggle_item(path.split("/")[3], body.get("item_id", ""), body.get("checked", False))))
        except (KeyError, ValueError) as error: return self.send_json(400, {"error": str(error)})
        except Exception as error: return self.send_json(502, {"error": f"Generation service unavailable: {error}"})
        return self.send_json(404, {"error":"not found"})


def recover_interrupted_import_jobs() -> None:
    for job in STORE.get_import_jobs():
        if job.get("status") in {"queued", "running"}:
            STORE.update_import_job(job["id"], status="failed", stage="Interrupted by service restart", error="The source was saved. Tap retry to run it again.")


def main():
    recover_interrupted_import_jobs(); resume_generation_jobs()
    print(f"Oui, Chef running on 0.0.0.0:{PORT}", flush=True); ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
if __name__ == "__main__": main()
