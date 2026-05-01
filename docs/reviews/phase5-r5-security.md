**Findings**

- CRITICAL | [docker-compose.yml](/home/ps/papers/QuantMind/docker-compose.yml:5) | Confidence: High | Issue: MongoDB and Redis publish `27017`/`6379` on all interfaces by default, with no auth shown. | Fix: bind to `127.0.0.1:27017:27017` and `127.0.0.1:6379:6379`, or remove host ports and use a local-only access path.

- CRITICAL | [backend/api/analysis.py](/home/ps/papers/QuantMind/backend/api/analysis.py:523) | Confidence: High | Issue: `/api/analysis/jobs` and `/stock` are unauthenticated and unthrottled; one caller can burn LLM budget and CPU. | Fix: require auth, add nginx `limit_req`, and enforce app-level job concurrency/admission limits.

- HIGH | [backend/services/analysis_stream.py](/home/ps/papers/QuantMind/backend/services/analysis_stream.py:207) | Confidence: High | Issue: SSE subscribers are unbounded per job; each creates a queue and open stream. | Fix: cap subscribers per job/IP, add nginx `limit_conn`, and reject excess streams with `429`.

- HIGH | [backend/monitoring/alerter.py](/home/ps/papers/QuantMind/backend/monitoring/alerter.py:136) | Confidence: High | Issue: webhook delivery failures log `str(exc)`; `httpx` errors can include the full webhook URL, often containing a secret token. | Fix: log only exception class/status code, and redact URLs before logging.

- HIGH | [scripts/backup.sh](/home/ps/papers/QuantMind/scripts/backup.sh:21) | Confidence: High | Issue: backups default into repo `backups/`, are not git-ignored, and the current archive is untracked with mode `0664`. | Fix: move default to `/var/backups/quantmind` or `~/.local/state/quantmind/backups`, `umask 077`, `chmod 600`, and add `backups/` to `.gitignore`.

- HIGH | [deploy/quantmind-backend.service](/home/ps/papers/QuantMind/deploy/quantmind-backend.service:16) | Confidence: High | Issue: the unit loads repo `.env`; local `.env` is mode `0664` and contains non-placeholder config. | Fix: make runtime env files `0600`, owned by service user, and keep any secret-like values outside the repo tree.

- HIGH | [deploy/quantmind-backend.service](/home/ps/papers/QuantMind/deploy/quantmind-backend.service:29) | Confidence: High | Issue: `--log-config config/logging.yaml` points to a file that does not exist, so the service is likely to fail at startup. | Fix: remove the flag or commit a valid uvicorn logging config.

- HIGH | [backend/api/monitoring.py](/home/ps/papers/QuantMind/backend/api/monitoring.py:131) | Confidence: High | Issue: dashboard reads `registry.circuit_breaker`, but startup stores it on `app.state.circuit_breaker`; ops will see `"unknown"` during a halt. | Fix: read `request.app.state.circuit_breaker` and expose `halted`, PnL, and consecutive losses.

- HIGH | [backend/api/trading.py](/home/ps/papers/QuantMind/backend/api/trading.py:353) | Confidence: High | Issue: `CircuitBreakerHaltedError` from approval is not caught, so halted approvals become 500s. | Fix: catch it and return `409` or `503`, leaving the approval pending and emitting an alert/event.

- MEDIUM | [deploy/nginx-quantmind.conf](/home/ps/papers/QuantMind/deploy/nginx-quantmind.conf:56) | Confidence: High | Issue: nginx has TLS but no auth, security headers, or request/connection limits on API/SSE locations. | Fix: add an auth layer, HSTS where appropriate, `X-Content-Type-Options`, frame policy, `limit_req`, and `limit_conn`.

No real API keys were found in the Phase 5 deploy examples; `replace-me` placeholders are fine.

**Ops Checklist**

1. Bind MongoDB and Redis to localhost only.
2. Confirm `.env` and `llm.env` are `0600`.
3. Ensure `/home/ps/.config/quantmind/llm.env` exists before enabling systemd.
4. Fix or remove `--log-config config/logging.yaml`.
5. Add auth before exposing `/api/*`.
6. Add rate limits for `/api/analysis/jobs` and SSE streams.
7. Move backups outside the repo and ignore `backups/`.
8. Run and restore-test one Mongo backup.
9. Verify monitoring shows actual circuit breaker halt state.
10. Run `daily-check.sh` after reboot and require a clean report before evaluation.