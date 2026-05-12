# RedLens AI an adversarial AI security evaluation platform focusing on repeatable, observable evaluation of the OpenEMR Clinical Co-Pilot sidecar endpoints:

- `POST /v1/chat`
- `POST /v1/documents/extract`

The first implementation is intentionally not a full autonomous red-team platform. It establishes the substrate: threat model, target registry, executable evaluations, deterministic judging, run history, findings, and a control panel.

## Local Development

Backend:

```bash
cd backend
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
