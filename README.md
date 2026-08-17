# oc-stats

Minimal all-time usage stats for OpenCode.

```bash
pipx install git+https://github.com/imluckii/oc-stats.git
oc-stats
```

`oc-stats` reads assistant-message usage from the local OpenCode database and
groups tokens by provider, model, and variant. Cost is estimated from current
standard first-party API list prices; subscription charges may differ.

```bash
oc-stats          # Rich terminal report
oc-stats --json   # machine-readable output
oc-stats tui      # interactive TUI (needs the tui extra)
oc-stats --db "C:\Users\Anchit\.local\share\opencode\opencode.db"
```

All discovered databases are merged automatically. Repeating `--db` merges
explicit paths; messages copied between databases are deduplicated by ID.

Requires Python 3.10+. V1 and V2 database schemas are supported; service fallback
requires OpenCode V2 (`opencode2` or `opencode`) on `PATH`.
Database access is read-only. If no local database exists, the tool falls back to
read-only requests through OpenCode's built-in `api` command.

## TUI

Install with the extra and run:

```bash
pipx install --force "oc-stats[tui] @ git+https://github.com/imluckii/oc-stats.git"
oc-stats tui
```

Five tabs: **Overview** (totals, top models, share of cost), **Models** (full
per-model table), **Daily** and **Hourly** breakdowns, and **Stats** (cache hit
rate, provider splits, busiest day/hour, unpriced turn ratio). Costs come from
the same `prices.toml` catalog as the report.

Keys:

- `←`/`→` or `Tab`/`Shift+Tab` switch tabs
- `c` / `t` / `p` sort models by cost / tokens / provider
- `d` cycles the date range: all time → today → 7 days → 30 days
- `i` toggles full/compact number formatting
- `r` refreshes now; `R` toggles auto-refresh (default off); `+`/`-` adjusts
  the interval in 30s steps (30–600s)
- `e` exports the current view as JSON to `./oc-stats-export-<ts>.json`
- `q` quits

Preferences (active tab, sort, number format, refresh interval) persist to
`~/.config/oc-usage/tui.toml`. Day/hour grouping uses IST by default; set
`OC_STATS_TZ=UTC` to override.

## Prices

Estimates come from the bundled `src/oc_usage/prices.toml` — generated from
the full [models.dev](https://models.dev) catalog (175 providers, 6,000+
models, gateway/reseller prices included as-is) and refreshed weekly through
a reviewable pull request. Prices are US dollars per 1M tokens, per provider,
with cache tiers and long-context tiers where models.dev publishes them.
Models without published pricing get no estimate rather than a guess.

A small hand-verified `OVERRIDES` list in `scripts/update_prices.py` pins the
few first-party entries where models.dev disagrees with the provider's
official pricing page (OpenAI fast-tier variants, day-one releases, Cursor's
grok variant). Subscription gateways (`zai-coding-plan`,
`minimax-coding-plan`, …) are listed at $0/token by models.dev; the generator
backfills them with the underlying model's first-party API list price, while
genuinely free tiers keep their $0.

Matching is provider-aware: `anthropic/claude-opus-5` from any gateway
resolves to Anthropic's rate, dated release suffixes fall back to the base
model, and a bare model name is used only when every provider prices it
identically — with resellers included, most common names are ambiguous and
correctly refuse to guess, so provider-qualified ids are preferred.

### Editing prices

Create your own override containing only the entries you want to change — it
is merged over the bundled list, and a matching block replaces the bundled
entry entirely:

```toml
# ~/.config/oc-usage/prices.toml   (or $OC_USAGE_PRICES)
[opencode."grok-code"]
input = 0.5
output = 2.0
```

Check any lookup with:

```bash
python -m oc_usage.pricing anthropic claude-opus-5
```

The generated file documents itself at the top of `prices.toml`; regenerate
locally with `python scripts/update_prices.py`.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest -q && python -m build
```

MIT licensed.
