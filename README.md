# RedLens AI an adversarial AI security evaluation platform focusing on repeatable, observable evaluation of the OpenEMR Clinical Co-Pilot sidecar endpoints:

| Resource | Link |
| --- | --- |
| Docs | [docs/](docs/) |
| Architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Threat model | [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) |
| Threat registry (app data) | [backend/app/data/threat_registry.md](backend/app/data/threat_registry.md) |

- `POST /v1/chat`
- `POST /v1/documents/extract`

The first implementation is intentionally not a full autonomous red-team platform. It establishes the substrate: threat model, target registry, executable evaluations, deterministic judging, run history, findings, and a control panel.

## Local Development

Start local Postgres:

```bash
docker compose up -d postgres
```

Backend:

```bash
cd backend
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend:

```bash
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

Then open `http://127.0.0.1:5173`.

## Verification

```bash
cd backend
uv run pytest

cd ../frontend
npm run build
```

The default local app database is Postgres at
`postgresql+psycopg://redlens:redlens@127.0.0.1:5432/redlens`. Fast backend
tests still use in-memory SQLite.

## LLM-Assisted Exploration

Exploration campaigns default to deterministic mode and do not require model
credentials. To run `llm_assisted` campaigns, configure OpenRouter:

```bash
OPENROUTER_API_KEY=...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_SITE_URL=http://localhost:5173
OPENROUTER_APP_TITLE=RedLens
REDLENS_RED_TEAM_MODEL=<openrouter-model-id>
```

The first LLM-assisted slice uses OpenRouter only for Red Team attack-plan
generation. The existing deterministic target execution and judging paths remain
available without credentials.

## Live OpenEMR Targets

The default seeded target is deterministic mock mode. Live targets route through the `oe-ai-agent` sidecar, which mints short-lived OpenEMR FHIR tokens on RedLens's behalf via its `POST /v1/openemr/mint-token` endpoint. RedLens never sees `INTERNAL_AUTH_SECRET` or holds a long-lived OpenEMR bearer token itself.

### One-time setup

1. Issue an API key on the agent side and paste the hash into `OE_AI_AGENT_API_KEY_HASHES` on the agent service:

   ```bash
   cd /path/to/oe-ai-agent
   uv run python -m oe_ai_agent.admin.issue_api_key --label rl-local
   ```

2. Set `OE_AI_AGENT_API_KEY=<token from step 1>` on RedLens. For local dev export it in your shell or put it in `.env`.

### Creating a live target

Create the target via the UI or `POST /api/targets` with:

- `mode: "live"`
- `base_url`: the agent's URL (e.g. `http://localhost:8400` for the dev-easy compose stack, or `http://oe-ai-agent.railway.internal:8000` in Railway)
- `user_uuid`: the OpenEMR `users.uuid` the agent should mint a token for (the token inherits that user's FHIR ACL)
- `patient_uuid` (optional): supplied to the agent for chat/brief calls
- `fhir_base_url` (optional): forwarded to the agent in the request body

RedLens caches the minted token in-process for ~4 minutes per (user, scope), so a 12-evaluation run typically issues a single mint call.
