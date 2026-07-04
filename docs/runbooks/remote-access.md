# Remote Access Lifecycle — Operator Runbook

> **D451 (Chunk 66).** If this document conflicts with `src/api/auth_middleware.py` or `src/api/main.py`, the code is canonical — file an issue to update this doc.

## 1. `GRACE_REMOTE_ACCESS_ENABLED` Lifecycle

1. Set `GRACE_REMOTE_ACCESS_ENABLED=true` in `.env` and restart uvicorn.
2. Issue a support token: `POST /api/admin/support-sessions` (requires `X-Admin-Key` header). Store the one-time bearer token returned in the response body — it is never persisted server-side.
3. Share the bearer token with the remote support engineer. They present it as `Authorization: Bearer support:<token>` on each request.
4. When support is complete, revoke the session: `POST /api/admin/support-sessions/{session_id}/revoke`.
5. Set `GRACE_REMOTE_ACCESS_ENABLED=false` in `.env` and restart uvicorn.

**Orphan-session escape:** if a session expires or the operator loses the session ID, `GET /api/admin/support-sessions` (admin-key gated) lists all sessions; expired sessions are visible but inactive.

## 2. `GRACE_CORS_ORIGINS` Discipline

- In production, set `GRACE_CORS_ORIGINS` to the exact frontend origins (comma-separated, no trailing slashes).
  Example: `GRACE_CORS_ORIGINS=https://grace.example.com,https://admin.example.com`
- When unset or empty, `_parse_cors_origins()` falls back to localhost dev origins (`http://localhost:3000`, `http://127.0.0.1:3000`) with a structlog WARN.
- Narrow origins in production — do not use wildcard `*`.

## 3. `GRACE_ADMIN_KEY` — the Load-Bearing Backend Gate

- All mutating routes require the `X-Admin-Key` header when `GRACE_ADMIN_KEY` is set in `.env`.
- When `GRACE_ADMIN_KEY` is unset, requests from `127.0.0.1` / `::1` are admitted (localhost dev bypass); non-loopback mutating requests are rejected with 401.
- CORS `allow_methods` is a browser-side admission filter, not an authorization mechanism. The admin-key gate is the authorization boundary.

## 4. Reverse-Proxy Checklist

If GrACE is deployed behind a reverse proxy (nginx, Caddy, Traefik, etc.):

1. Ensure the proxy forwards PATCH, DELETE, PUT, and OPTIONS requests without stripping or rewriting the HTTP method.
2. Ensure the `Access-Control-Allow-Methods` response header is not overwritten or stripped by the proxy — the header must pass through from the FastAPI CORS middleware unchanged.
3. Test with `curl -X OPTIONS` from the expected origin to verify end-to-end preflight:
   ```bash
   curl -s -D- -o /dev/null -X OPTIONS https://grace.example.com/api/ontology/daemon/kill-switch \
     -H 'Origin: https://admin.example.com' \
     -H 'Access-Control-Request-Method: PATCH'
   ```
   Verify the response includes `Access-Control-Allow-Methods` with `PATCH` listed.
