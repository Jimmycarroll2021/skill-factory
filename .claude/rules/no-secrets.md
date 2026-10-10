---
paths:
  - "**/*"
---
# No secrets in tracked files

- Never write API keys, access tokens, passwords, private keys, connection strings with credentials, or values copied from `.env` files into any tracked file. That covers source, config, tests, fixtures, docs and commit messages.
- Read secrets from environment variables at run time. Document each one's *name* in `.env.example` with an empty or placeholder value, never the real value.
- Never create, stage or commit `.env`, `.env.local`, `.env.production` or similar files. Only `*.example`, `*.sample` and `*.template` variants may be tracked.
- If a secret is found in a tracked file, stop and report its location (file and line). Do not repeat the value in output, and do not try to "fix" it by rewriting history.
