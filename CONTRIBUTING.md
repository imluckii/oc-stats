# Contributing to oc-stats

Thanks for your interest! `oc-stats` is small on purpose — keep it that way.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Before opening a PR

```bash
ruff check .
ruff format .
pytest -q
```

## Guidelines

- **Keep the service transport in `src/oc_usage/service.py`.** It is the only
  thing that talks to OpenCode (via the `api` command) — never open databases or
  raw sockets. If you change how a response is parsed, add/adjust a synthetic
  fixture in `tests/helpers.py` and a test in `tests/test_service.py`.
- **Never estimate tokens.** Every token count must trace back to a recorded
  field. If a field is missing, treat it as `0` and document it.
- **Never invent prices.** Estimated *cost* is allowed only via
  `src/oc_usage/prices.toml`, and every entry there must be verifiable against
  the provider's official pricing page on the day it is added — when in doubt,
  skip the model and say so in the PR. Update the `updated` field when prices
  change, and cite the source in the section comment.
- **Fail loudly, never silently report zero.** An incompatible API, invalid JSON,
  a missing executable, or a broken price file must raise, not produce an empty
  report.
- **No real session data in tests or docs.** Fixtures and README samples must be
  synthetic, and message **content** must never appear in reports.
- Use [Conventional Commits](https://www.conventionalcommits.org/) with a scope,
  e.g. `feat(cli): ...`, `fix(service): ...`, `docs(readme): ...`.

By contributing, you agree your changes are licensed under the MIT License.
