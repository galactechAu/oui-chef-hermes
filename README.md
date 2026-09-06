# Oui, Chef

A small, shared meal planner for high-protein, lower-carb cooking: generate a plan with Hermes, keep a live shopping list, save recipes, and review imports before they become meals.

## Features

- Dietary-allergy settings: mushroom protection is always enabled; add household allergy terms to screen both generated suggestions and imports before review.
- Shared shopping lists with Server-Sent Events (SSE) updates.
- Responsive mobile layout with fixed bottom navigation.
- Saved meals, cooking mode, ratings, and serving scaling.
- Review-first recipe imports from public webpages, supported public media, pasted text, and images.
- Local transcription/OCR dependencies are included in the Docker image; public access only—no login or access-control bypass.

## Quick start

Requirements: Git, Docker Engine, and the Docker Compose plugin. Hermes is optional unless you want AI meal generation or AI-assisted imports.

```bash
git clone <your-fork-or-repository-url> oui-chef
cd oui-chef
cp .env.example .env
docker compose up --build -d
curl -fsS http://127.0.0.1:8094/health
```

Open `http://localhost:8094`. Stop with `docker compose down`; your named `oui-chef-data` volume is retained. To remove data too: `docker compose down -v` (destructive).

## Hermes integration

Lists and saved meals work without Hermes. To enable generation and AI-assisted importing, configure a **private** bridge URL in `.env`:

```dotenv
HERMES_BRIDGE_URL=http://host.docker.internal:8095/generate
```

See [docs/hermes-integration.md](docs/hermes-integration.md). Never expose the bridge on the public internet or put credentials in `.env.example`.

## Safety and content

Mushrooms are always screened as a household allergy, and you can add other dietary allergies in the Plan tab. Configured terms are included as hard generation constraints and are checked across generated recipe text and imported recipe titles, ingredients, summaries, and methods before a recipe can reach review or Meals. This is an ingredient-text safety screen, not medical or nutritional advice; always check labels and source recipes. Imported content remains subject to its source’s terms and rights; preserve attribution and use only legitimately public material.

## Development

Use Python 3.13 and Node.js 22 for the validation suite. Node is used only to check JavaScript syntax; the application server remains dependency-light Python.

```bash
python3 -m unittest discover -s tests -q
python3 -m py_compile app.py core.py store.py generation.py recipe_importer.py recipe_page.py realtime.py allergies.py
python3 scripts/check_javascript.py
python3 scripts/public_safety_scan.py
# Before a commit, also scan the exact staged content:
python3 scripts/public_safety_scan.py --staged
git diff --check
docker compose config --quiet
```

Syntax and unit checks do not replace interactive desktop/mobile, keyboard, and multi-client acceptance testing. To run the opt-in browser suite, install `playwright==1.62.0` in a development virtual environment, run `python -m playwright install chromium`, and set `OUI_BROWSER_EXECUTABLE` to that browser's executable path. Then run the normal test discovery with the environment variable set; the browser tests use disposable synthetic stores. CI runs these browser checks separately.

The public-safety scanner detects known high-confidence patterns; review all proposed files and reachable history before publication.

See [AGENTS.md](AGENTS.md), [CONTRIBUTING.md](CONTRIBUTING.md), and [docs/architecture.md](docs/architecture.md).

## License

This repository is dedicated to the public domain under [CC0-1.0](LICENSE), to the extent contributors hold rights in it. See [NOTICE.md](NOTICE.md) for third-party-content limitations.
