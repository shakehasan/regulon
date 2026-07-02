# Security Policy

## Supported versions

Regulon is pre-1.0. Only the latest release (and `main`) receive security fixes.

| Version | Supported |
|---|---|
| latest release / `main` | Yes |
| older tags | No |

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Report privately via GitHub Security Advisories:
[https://github.com/shakehasan/regulon/security/advisories/new](https://github.com/shakehasan/regulon/security/advisories/new)

Include reproduction steps, affected component (e.g., API auth, audit chain, redaction), and impact.
You can expect an acknowledgment within 7 days.

## Scope notes

- Regulon's default runtime is local-first; the FastAPI service ships with simple local token auth and
  is not hardened for public internet exposure. Deployments should sit behind their own network
  controls. The threat model (from M6) is documented in `docs/threat_model.md`.
- The audit log is tamper-evident (hash-chained), not tamper-proof: it detects modification, it does not
  prevent an attacker with filesystem write access from truncating history. See ADR-008 when available.
