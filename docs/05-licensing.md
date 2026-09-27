# 05 — Licensing

This is the design rationale. The actual terms are in the root [Apache 2.0 license](../LICENSE) and the [server's Elastic License 2.0](../server/LICENSE). Read the applicable license before relying on this summary.

## Decision: split by layer

| Layer | License | Why |
|---|---|---|
| Spec, compiler, CLI, SDKs, engine adapters | **Apache 2.0** | Adoption layer. A spec only becomes a standard if permissive |
| Server: review-queue UI, online serving, trace ingestion | **Elastic License 2.0** | What a cloud would host → what we protect |

## Notes on ELv2

- Permits use, modification, redistribution. Forbids: providing it as a hosted/managed service, circumventing license keys, removing notices.
- **Not OSI "open source."** Call it source-available or expect pushback. Some enterprise legal teams block non-OSI licenses; distros won't package.
- The split was chosen when the project was first licensed, rather than changing an existing license later.

## Alternative considered

**FSL (Functional Source License, Sentry's):** source-available now, converts to Apache/MIT after 2 years. Stronger trust signal if a single license for everything is preferred.

## Reality check

The real moat is operating the service (review workflow, trace ingestion at scale, calibration monitoring), not the license. The license only stops the laziest competitor.

## Implementation status

- [x] Root `LICENSE` (Apache 2.0) and `NOTICE` added 2026-09-24 for the core package.
- [x] `server/LICENSE` (ELv2) added with the server.
- [ ] Decide whether contributions need a DCO or CLA before accepting outside code.
