# oc-stats

Minimal all-time usage stats for OpenCode V2.

```bash
pipx install git+https://github.com/imluckii/oc-stats.git
oc-stats
```

`oc-stats` reads assistant-message usage from the running OpenCode service and
groups tokens by provider, model, and variant. Cost is estimated from current
standard first-party API list prices; subscription charges may differ.

```bash
oc-stats          # Rich terminal report
oc-stats --json   # machine-readable output
```

Requires Python 3.10+ and OpenCode V2 (`opencode2` or `opencode`) on `PATH`.
The tool only makes read-only requests through OpenCode's built-in `api` command.

Model prices are generated from [models.dev](https://models.dev/) and refreshed
weekly through a reviewable pull request.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest -q && python -m build
```

MIT licensed.
