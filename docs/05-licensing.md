# 05 — Licensing

Goal: free for anyone to use, modify, self-host; nobody else may offer it as a hosted/managed service.

## Decision: split by layer

| Layer | License | Why |
|---|---|---|
| Spec, compiler, CLI, SDKs, engine adapters | **Apache 2.0** | Adoption layer. A spec only becomes a standard if permissive |
| Server: review-queue UI, online serving, trace ingestion | **Elastic License 2.0** | What a cloud would host → what we protect |

## Notes on ELv2

- Permits use, modification, redistribution. Forbids: providing it as a hosted/managed service, circumventing license keys, removing notices.
- **Not OSI "open source."** Call it source-available or expect pushback. Some enterprise legal teams block non-OSI licenses; distros won't package.
- Precedent in this ecosystem: dbt Labs used ELv2 for the Fusion engine (from memory of the 2025 launch; verify).
- The Elastic / HashiCorp / Redis backlash (→ OpenTofu, Valkey forks) was about **relicensing**, not the licenses themselves. Pick day one, never change.

## Alternative considered

**FSL (Functional Source License, Sentry's):** source-available now, converts to Apache/MIT after 2 years. Stronger trust signal if a single license for everything is preferred.

## Reality check

The real moat is operating the service (review workflow, trace ingestion at scale, calibration monitoring), not the license. The license only stops the laziest competitor.

## TODO

- [x] Root `LICENSE` (Apache 2.0) + `NOTICE` added 2026-09-24; covers all current code.
- Add `server/LICENSE` (ELv2) together with the first server code.
- CLA or DCO for contributions (needed if relicensing the Apache part is ever possible; DCO is friendlier).
