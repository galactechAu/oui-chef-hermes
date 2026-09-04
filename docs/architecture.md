# Architecture

Oui, Chef is intentionally dependency-light: a Python standard-library HTTP server serves a static HTML/CSS/JS client and stores durable state in JSON using atomic writes.

## Components

- **API/UI:** `app.py` routes HTTP APIs and serves `static/index.html`.
- **Persistence:** `store.py` saves lists, imported recipes, import jobs, and history.
- **Realtime:** mutations persist first, then `realtime.EventHub` broadcasts an SSE `state.changed` signal. Browsers reload durable state after receiving it; a short polling fallback covers disconnected clients.
- **Recipes:** `recipe_page.py` renders a self-contained cook-mode page. Browser timer and completion state remains local to the browser.
- **Importing:** `recipe_importer.py` validates public URLs, bounded input, image data, and source classification. Imports retain source attribution and require review before saving.
- **AI:** `generation.py` validates structured meal output. `app.py` calls an optional `HERMES_BRIDGE_URL`; the bridge is outside the public application boundary and must be private.

## Security boundaries

Only `http`/`https` public URLs are considered for imports; local/private/reserved targets and unsafe redirects are rejected. Public-media transcription is bounded and does not use cookies, login sessions, or access-control bypass. The Hermes bridge must be private/authenticated by the operator’s chosen network controls.

## Persistence

Docker Compose mounts the named `oui-chef-data` volume at `/app/data`. Back it up before upgrades. Do not delete it unless intentionally resetting all saved data.
