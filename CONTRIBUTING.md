# Contributing

Thanks for improving Oui, Chef. Keep changes small, tested, and safe for shared household data.

## Before a change

- Read `AGENTS.md` and the relevant module/tests.
- Preserve mushroom exclusion, source attribution, review-first imports, and private bridge boundaries.
- Do not commit `.env`, data, credentials, private host details, or third-party recipe content without rights.

## Validation

```bash
python3 -m unittest discover -s tests -q
python3 -m py_compile app.py core.py store.py generation.py recipe_importer.py recipe_page.py realtime.py
docker compose config
```

Use test-first development for behavior changes. UI changes need browser validation at mobile and desktop widths. For shopping-list changes, confirm another open browser receives the SSE update without manual reload.

## Documentation

Public docs must describe portable application concepts only. Do not add user-specific topology, credentials, private paths, or vendor-specific hosting instructions.
