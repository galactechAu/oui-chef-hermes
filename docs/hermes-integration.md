# Hermes integration

Hermes is optional for browsing saved meals and managing shopping lists. It is required for AI meal generation and AI-assisted recipe extraction.

## Requirements

- Hermes Agent installed, authenticated, and configured with a model.
- Docker Engine and Compose if running the provided container setup.
- A **private** HTTP bridge implementing `POST /generate` with JSON input `{"prompt":"..."}` and output `{"ok":true,"output":"..."}` (or `{"ok":false,"error":"..."}`).

Set its URL in `.env`:

```dotenv
HERMES_BRIDGE_URL=http://host.docker.internal:8095/generate
```

Do not expose the bridge publicly. Keep it within your own private, authenticated network boundary. Never place Hermes credentials in the application repository.

## Agent setup prompt

> Clone this repository, read `README.md` and `AGENTS.md`, and verify Docker/Compose and Hermes prerequisites. Copy `.env.example` to `.env`; ask the operator for a private `HERMES_BRIDGE_URL` rather than guessing. Start the app with `docker compose up --build -d`, verify `/health`, and test normal list functionality. Before enabling AI generation, verify that the bridge is private and does not expose credentials. Do not modify or delete persistent data, bypass external-platform access controls, or commit secrets.

## Troubleshooting

If a generation action says the bridge is unconfigured or unavailable, verify the value of `HERMES_BRIDGE_URL`, bridge reachability from inside the application container, and Hermes authentication. Keep troubleshooting output free of secrets.
