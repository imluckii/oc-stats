# oc-usage

> All-time **OpenCode** token usage, straight from the local session database.

`oc-usage` reads assistant turns from OpenCode's SQLite database and reports token
usage — broken down by **provider**, **model**, and **variant** — with **cost**
when it is tracked. It understands both the **v1** (`message`) and **v2**
(`session_message`) database schemas and reconciles every number from the raw
recorded components.

- 🔒 **Read-only.** Opens the database in SQLite `mode=ro`; never writes, never
  sends data anywhere.
- 🧮 **Accurate.** Totals are summed from per-turn `input` / `cache_read` /
  `cache_write` / `output` / `reasoning` components — never estimated.
- 💸 **Honest about cost.** Cost is shown only when actually recorded. If every
  recorded cost is zero, it says *not tracked*.
- 🎨 **Beautiful by default** (Rich), with `--plain`, `--full`, and `--json` modes.
- 📦 **Single dependency:** [`rich`](https://github.com/Textualize/rich).

---

## Sample output

Default (compact) report:

```text
  ╔══════════════════════════════════════════════════════════════════════╗
  ║                                                                        ║
  ║    1,204 turns  ·  3 providers  ·  5 models                            ║
  ║    2025-06-14 → 2025-07-30                                             ║
  ║                                                                        ║
  ╚══════════════════════════════════════════════════════════════════════╝

  ────  Token Totals  ────

  Input (non-cache)        5,432,110   41% of input
  Cache read               7,812,904   cached
  Output                   1,204,338
  Reasoning                  512,007
  ─────────────────────────────────────
  Total                   14,961,359
  Cost                        $24.18

  ────  Cache  ────

  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  58.9%  cache hit

  ────  By Provider  ────

   Provider         Turns       Input      Cache     Output  Reasoning      Total      Cost
   ────────────────────────────────────────────────────────────────────────────────────────
   zai-coding-plan   1,000   4,000,000  7,000,000   1,000,000  500,000  12,500,000   $12.00
   openai              184   1,000,000          0      184,338    8,002   1,192,340   $12.00
   cerebras             20     432,110     812,904     20,000    4,005   1,269,019       —

  ────  By Model  ────

    zai-coding-plan
    Model          Variant  Turns    Input   Cache   Output  Reasoning      Total      Cost
    glm-4.7        default   1,000  4.00M   7.00M   1.00M     500K    12.50M   $12.00
    ...
```

> The numbers above are **synthetic** and illustrative. `oc-usage` computes real
> figures from your own database.

Machine-readable JSON (`--json`):

```json
{
  "totals": {
    "turns": 1204,
    "input": 5432110,
    "output": 1204338,
    "reasoning": 512007,
    "cache_read": 7812904,
    "cache_write": 0,
    "total": 14961359,
    "cost": 24.18,
    "cost_tracked": true
  },
  "providers": { "zai-coding-plan": { "...": "..." } },
  "models": [
    { "provider": "zai-coding-plan", "model": "glm-4.7", "variant": "default", "total": 12500000 }
  ],
  "span": { "from": "2025-06-14T00:00:00Z", "to": "2025-07-30T00:00:00Z" }
}
```

---

## Install

Requires Python **3.10+**. Recommended: install isolated with **pipx**.

```bash
pipx install git+https://github.com/imluckii/oc-usage.git
```

Or with plain pip into a virtualenv:

```bash
python -m venv .venv && source .venv/bin/activate
pip install git+https://github.com/imluckii/oc-usage.git
```

Then run:

```bash
oc-usage          # auto-detected database, compact numbers
oc-usage --full   # full integers (no K/M rounding)
oc-usage --json   # machine-readable JSON
```

You can also run it without installing, as a module:

```bash
python -m oc_usage
```

---

## Usage

```
usage: oc-usage [-h] [--db PATH] [--full] [--no-color] [--plain] [--json] [--version]

All-time OpenCode token usage from the local session database. Reads assistant
turns (OpenCode v1 and v2 schemas) and reports tokens by provider, model, and
variant, with cost when tracked.

options:
  --db PATH        path to an opencode .db file (auto-detected by default; v2 preferred)
  --full           show full integers (no K/M rounding)
  --no-color       disable ANSI colors
  --plain          alias for --no-color
  --json           emit JSON to stdout
  --version        show version and exit
  -h, --help       show this help message and exit
```

### Selecting a database (`--db`)

By default `oc-usage` auto-detects the database, preferring the active **v2**
database. To inspect a specific one:

```bash
# v2 (newer / opencode-next)
oc-usage --db ~/.local/share/opencode/opencode-next.db

# v1 (legacy)
oc-usage --db ~/.local/share/opencode/opencode.db
```

> ⚠️ **Don't point two invocations at different databases and add them up.**
> After migrating, history can be **duplicated** across `opencode.db` and
> `opencode-next.db`. `oc-usage` deliberately reads **one** database per run.
> Pick the one that holds your current history (usually v2).

### Inside OpenCode

From OpenCode's shell mode, run it inline:

```
!oc-usage
```

#### Optional global `/stats` command

To turn `oc-usage` into a first-class slash command, add an OpenCode command
file. In `~/.config/opencode/command/` (or your project's `.opencode/command/`)
create `stats.md`:

```markdown
---
description: All-time OpenCode token usage
---

!oc-usage
```

Then `/stats` runs `oc-usage` for you.

---

## Accuracy methodology

Every figure is derived from **persisted assistant turns**, each of which carries
its own `model` / `provider` / `tokens` record. `oc-usage`:

1. Streams `type = 'assistant'` rows (v2) or `$.role == 'assistant'` rows (v1).
2. Normalizes each turn to the same shape regardless of schema.
3. Sums the **explicit token components** per provider / model / variant.
4. Reconciles: the sum of all provider buckets equals the sum of all model
   buckets equals the grand total.

**Cost is never estimated.** It is the literal sum of the per-turn `cost` field
OpenCode recorded. If no turn recorded a non-zero cost, the report prints
`— not tracked by these providers`.

### Exact category definitions

| Category    | Meaning                                                              |
| ----------- | -------------------------------------------------------------------- |
| `input`     | Non-cached prompt tokens (`tokens.input`).                           |
| `cache_read`| Prompt tokens served from cache (`tokens.cache.read`).               |
| `cache_write` | Prompt tokens written to cache this turn (`tokens.cache.write`).   |
| `output`    | Completion tokens (`tokens.output`).                                 |
| `reasoning` | Reasoning/thinking tokens (`tokens.reasoning`).                      |
| `total`     | `input + cache_read + cache_write + output + reasoning` (computed).  |
| `turns`     | Count of assistant messages aggregated.                              |
| `cost`      | Sum of recorded per-turn `cost` (shown only when tracked).           |

For the **v1** schema, the stored `tokens.total` has been validated (against
fixtures and real data) to equal exactly this computed sum — but `oc-usage`
always recomputes it from the components to stay schema-independent and to keep
every category explicit.

---

## v1 vs v2 support

OpenCode changed its database schema between versions. `oc-usage` supports both
and detects which one to use **per database, based on actual rows** (not just
table existence):

|                | **v1** (`opencode.db`)                  | **v2** (`opencode-next.db`)               |
| -------------- | --------------------------------------- | ----------------------------------------- |
| Source table   | `message`                               | `session_message`                         |
| Assistant filter | JSON `$.role == 'assistant'`          | SQL column `type = 'assistant'`           |
| Model location | flat `modelID` / `providerID` / `variant` | nested `model: {id, providerID, variant}` |
| Tokens         | `tokens: {total, input, output, reasoning, cache:{read,write}}` | `tokens: {input, output, reasoning, cache:{read,write}}` |
| Cost           | `cost` (top-level)                      | `cost` (top-level)                        |

**Detection logic:** a migrating v1 database can contain a (mostly empty)
`session_message` table, and a v2 database can contain an empty `message` table.
So `oc-usage` picks the schema whose assistant query *actually returns rows* —
checking the v2 `session_message` first, then falling back to the v1 `message`
table. A single database is always read from exactly one source table, so history
is never double-counted.

### Default database locations

| OS      | Path                                                         |
| ------- | ------------------------------------------------------------ |
| Linux   | `~/.local/share/opencode/opencode-next.db` (v2, preferred)  |
| Linux   | `~/.local/share/opencode/opencode.db` (v1, legacy)           |
| macOS   | `~/Library/Application Support/opencode/...`                 |
| Windows | `%LOCALAPPDATA%\opencode\...`                                |

Auto-detection prefers `opencode-next.db`; override with `--db` any time.

---

## Privacy & read-only guarantee

- The database is opened with a SQLite `mode=ro` URI. `oc-usage` **cannot**
  modify your data and creates no sidecar files from reading.
- It reads **only** the columns it needs to sum tokens/cost (`data`, `type`).
- Nothing is transmitted off the machine — no telemetry, no network calls.
- An active write-ahead log (`-wal`) is safe: the read-only connection sees the
  last committed state without locking writers.

---

## Limitations

- **Cost availability.** Cost depends on whether your provider/account reports
  per-turn cost to OpenCode. When none is recorded, `oc-usage` reports *not
  tracked* rather than guessing.
- **Schema coupling.** If OpenCode changes its database shape, detection may
  need a small update. The code is intentionally small and centralizes schema
  handling in `oc_usage/db.py`.
- **Subagents included.** All assistant turns are counted, including those from
  sub-agents/build agents — they are indistinguishable in the schema and each
  carries its own model record.
- **One database per run.** To avoid duplicate-counting across a migration,
  `oc-usage` reads a single database. Sum across `--db` invocations at your own
  risk.

---

## Development

```bash
git clone https://github.com/imluckii/oc-usage.git
cd oc-usage
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Run the checks:

```bash
ruff check .          # lint
ruff format --check . # format check
pytest -q             # tests (synthetic v1/v2 SQLite fixtures)
python -m build       # build sdist + wheel into dist/
```

### Package layout

```
src/oc_usage/
├── __init__.py   # version
├── __main__.py   # python -m oc_usage
├── cli.py        # argparse + entry point
├── db.py         # discovery, schema detection, row loading (read-only)
├── models.py     # normalized rows, buckets, aggregation
└── render.py     # Rich report + JSON output
```

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Released under the
[MIT License](LICENSE).
