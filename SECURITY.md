# Security Policy

## Reporting a vulnerability

Found a security issue in LynxAct Coach? Please email **founder@lxlynx.com**
with a description and reproduction steps. Do **not** open a public GitHub
issue — give us a chance to investigate and patch first.

## Response

We'll acknowledge the report and work with you on a fix and coordinated
disclosure. This is a small project with no formal SLA, but reports are taken
seriously.

## Scope

This policy covers the `lynxact-coach` repository (the Flask tactical-analysis
app). The marketing site at lynxact.lxlynx.com is static and collects no
personal data beyond what its host (Cloudflare) logs — see its
[/privacy.html](https://lynxact.lxlynx.com/privacy.html).

## What we consider in scope

- Path traversal, injection, or auth bypass in the Flask routes.
- Unsafe handling of uploaded media or LLM/tool-call output.
- Secrets or credentials committed to the repo.

## Out of scope

- The localhost demo runs on 127.0.0.1 by default; attacking it requires local
  access, which is the operator's own environment.
- DDoS or availability issues against the demo server.
