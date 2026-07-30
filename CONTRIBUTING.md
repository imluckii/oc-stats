# Contributing to oc-usage

Thanks for your interest! `oc-usage` is small on purpose — keep it that way.

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

- **Keep schema logic in `src/oc_usage/db.py`.** If you add or change how a
  schema is parsed, add/adjust a synthetic fixture in `tests/helpers.py` and a
  test in `tests/test_db.py`.
- **Never estimate cost or tokens.** Every number must trace back to a recorded
  field. If a field is missing, treat it as `0` and document it.
- **One database per run.** Do not add a mode that silently sums multiple
  databases — migration can duplicate history. If you add such a mode, it must
  warn loudly about double-counting.
- **No real session data in tests or docs.** Fixtures and README samples must be
  synthetic.
- Use [Conventional Commits](https://www.conventionalcommits.org/) with a scope,
  e.g. `feat(cli): ...`, `fix(db): ...`, `docs(readme): ...`.

By contributing, you agree your changes are licensed under the MIT License.
