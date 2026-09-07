# oc-stats

Minimal retained-usage stats for OpenCode.

```bash
pipx install git+https://github.com/imluckii/oc-stats.git
oc-stats
```

`oc-stats` reads usage from the local OpenCode databases and groups tokens by
provider, model, and variant. Cost is estimated from current standard
first-party API list prices; subscription charges may differ.

```bash
oc-stats          # Rich terminal report
oc-stats mini     # same report, each model's variants merged into one row
oc-stats --json   # machine-readable output
oc-stats tui      # interactive TUI (needs the tui extra)
oc-stats --db "C:\Users\Anchit\.local\share\opencode\opencode.db"
oc-stats --db PATH tui   # --db is global and must precede the subcommand
```

All discovered databases are merged automatically — every `opencode*.db`
directly under the data root (channel databases like `opencode-beta.db`
included; `.bak` files and snapshot folders are ignored). Repeating `--db`
merges explicit paths; messages copied between databases are deduplicated by
ID.

Requires Python 3.10+. V1 and V2 database schemas are supported, including
mixed databases that carry both tables; service fallback requires OpenCode V2
(`opencode2` or `opencode`) on `PATH`. Database access is read-only. If no
local database exists, the tool falls back to read-only requests through
OpenCode's built-in `api` command.

## What is counted

Assistant messages provide the per-model detail, and OpenCode's per-session
usage ledger keeps the totals honest:

- **Sub-agents count.** Child sessions (`parentID`) are real separately-billed
  model calls and are included like any other session.
- **Fork copies don't count.** OpenCode forks copy the parent transcript into
  the fork with fresh message IDs and a zeroed usage ledger. Those rows are
  context, not new calls, and are excluded (a copied row always predates the
  fork's creation).
- **Internal usage is reconciled.** Title generation, compaction, and requests
  removed by a committed revert are added to the session ledger without a
  retained assistant message. The positive difference appears as one
  `(unattributed)` / `(internal usage)` row per session, carrying the cost
  OpenCode recorded for it.
- **Deleted sessions are gone.** OpenCode cascades session deletion, so every
  number is *retained* usage, not all-time usage.
- **Failed turns with recorded tokens count; turns without a token object
  count as unaccounted** (they mark the estimate incomplete instead of
  silently pricing at $0).

`oc-stats mini` prints the same report with every model's variants summed
into a single row (`gpt-5.6-sol · high/low/...` becomes one `gpt-5.6-sol`),
for when the effort-level split is more detail than you want.

Two costs are reported: **recorded cost** (what OpenCode billed at request
time, summed from its own data) and **estimated cost** (today's list prices
from the bundled catalog). They differ by design — subscriptions and gateways
record $0 or their own rates.

## Hiding providers and models

Providers or models you don't want in the report can be dropped entirely —
tables, totals, cost estimates, and the TUI's day/hour groupings are all
recomputed from what remains. List their ids in
`~/.config/oc-usage/config.toml`:

```toml
hidden_providers = ["zai", "openrouter"]
hidden_models = ["title-generation", "openai/gpt-4o"]
```

Provider ids match the report's Provider column, case-insensitively; the
synthetic `(unknown)` and `(unattributed)` rows hide the same way. A
`hidden_models` entry takes one of three forms: a bare model id (hidden under
every provider and variant), `provider/model` (any variant of that provider's
model), or `provider/model/variant` for one variant only — a trailing slash
(`provider/model/`) selects rows recorded with no variant. The header (and the
JSON `source` field) notes how many providers and models were hidden. A config
file that exists but is broken fails the run instead of silently showing
everything; set `OC_STATS_CONFIG` to read it from somewhere else.

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
`OC_STATS_TZ` to any IANA zone name (or `UTC`) to override.

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
