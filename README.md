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
oc-stats --db "C:\Users\Anchit\.local\share\opencode\opencode.db"
```

All discovered databases are merged automatically. Repeating `--db` merges
explicit paths; messages copied between databases are deduplicated by ID.

Requires Python 3.10+. V1 and V2 database schemas are supported; service fallback
requires OpenCode V2 (`opencode2` or `opencode`) on `PATH`.
Database access is read-only. If no local database exists, the tool falls back to
read-only requests through OpenCode's built-in `api` command.

## Prices

Estimates come from the bundled `src/oc_usage/prices.toml` — a hand-curated,
human-editable list where every entry was verified against the provider's
official pricing page (Anthropic, OpenAI, Google, xAI, DeepSeek, Mistral,
Moonshot, Z.AI, MiniMax, Groq, Cohere, Perplexity, Alibaba, Amazon Bedrock,
OpenCode Zen, and OpenRouter's live listings). Prices are US dollars per 1M
tokens, per provider, with cache tiers and long-context tiers where providers
publish them. Models whose pricing could not be verified are deliberately
absent — they get no estimate rather than a guess.

Matching is provider-aware: `anthropic/claude-opus-5` from any gateway resolves
to Anthropic's rate, OpenRouter-only discounts live under `[openrouter]`, and a
bare model name is used only when every provider prices it identically.

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

The full bundled file (fields, matching rules, per-provider sources) documents
itself at the top of `prices.toml`.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest -q && python -m build
```

MIT licensed.
