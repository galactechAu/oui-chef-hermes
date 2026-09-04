# Oui, Chef agent guide

## Purpose and safety invariants

Oui, Chef is a shared meal-planning and shopping-list app. Preserve these invariants:

- Exclude mushrooms and mushroom-derived products from generated/imported recipes and shopping lists.
- Imports are review-first; never silently save model-derived recipes.
- Preserve source attribution. Do not bypass logins, paywalls, robots, or platform access controls.
- Keep the Hermes bridge private. Never expose credentials or a generation endpoint publicly.
- Do not delete/migrate `/app/data`, change persistent/API identifiers, or run destructive commands without explicit owner approval.

## Layout

- `app.py`: HTTP API, SSE endpoint, optional Hermes bridge client.
- `store.py`: JSON persistence and atomic writes.
- `generation.py`, `recipe_importer.py`: validation and source handling.
- `realtime.py`: in-process SSE event hub.
- `recipe_page.py`, `static/index.html`: product UI.
- `tests/`: standard-library unittest suite.

## Required validation

```bash
python3 -m unittest discover -s tests -q
python3 -m py_compile app.py core.py store.py generation.py recipe_importer.py recipe_page.py realtime.py
docker compose config
```

For UI changes, browser-test desktop and mobile widths. The fixed bottom nav must retain safe-area support and enough content bottom padding.

## Repository hygiene

Never commit `.env`, runtime data, credentials, host paths, private IPs, or deployment-specific files. Public documentation must not identify a particular private hosting provider/topology. Use `.env.example` for non-secret configuration only.

## Hermes-aware setup

An operator’s Hermes needs an authenticated/configured model plus permission to use terminal/container tooling if it is asked to install or run Docker. Hermes is optional for list/meal use and required only for AI generation/import. Read `README.md` and `docs/hermes-integration.md` before changing integration behavior.
