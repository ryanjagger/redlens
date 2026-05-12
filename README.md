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

The default seeded target is deterministic mock mode. For live calls, create a `live` target in the UI and set env var names such as:

- `OPENEMR_INTERNAL_AUTH_SECRET`
- `OPENEMR_BEARER_TOKEN`
- `OPENEMR_FHIR_BASE_URL`

RedLens stores only the env var names. Actual secret values are read from the environment at run time and are redacted from stored run evidence.

### Mint a Local OpenEMR Bearer Token

The local dev-easy stack uses `dev-internal-auth-secret` unless `INTERNAL_AUTH_SECRET` is overridden. The OpenEMR bearer token is short-lived and user-scoped, so use the dev helper:

```bash
bash scripts/mint_openemr_token.sh --username admin --pid 1 --env
```

Then start RedLens with the printed exports:

```bash
cd backend
OPENEMR_INTERNAL_AUTH_SECRET=dev-internal-auth-secret \
OPENEMR_BEARER_TOKEN='<minted token>' \
OPENEMR_FHIR_BASE_URL='http://openemr/apis/default/fhir' \
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Tokens expire after five minutes. Re-mint before each live run.
