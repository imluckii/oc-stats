# oc-usage

> All-time **OpenCode** token usage, from a local database or one V2 server.

`oc-usage` reads assistant turns from OpenCode's SQLite database or from one
OpenCode V2 HTTP server and reports token usage — broken down by **provider**,
**model**, and **variant** — with **cost** when it is tracked. It understands
both the **v1** (`message`) and **v2** (`session_message`) database schemas and
the V2 server's paginated session/message API. Every number is reconciled from
raw recorded components.

- 🔒 **Read-only.** Opens local databases in SQLite `mode=ro`; server mode sends
  only authenticated GET requests and never writes to the server.
- 🧮 **Accurate.** Totals are summed from per-turn `input` / `cache_read` /
  `cache_write` / `output` / `reasoning` components — never estimated.
- 💸 **Honest about cost.** Cost is shown only when actually recorded. If every
  recorded cost is zero, it says *not tracked*.
- 🎨 **Beautiful by default** (Rich), with `--plain`, `--full`, and `--json` modes.
- 🪟 **Windows-ready.** Checks the XDG-style and native OpenCode data locations,
  uses safe SQLite file URIs, and has an explicit `--ascii` mode for legacy
  consoles.
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

   Provider         Turns       Input  Cache Read  Cache Write      Output  Reasoning      Total      Cost
   ────────────────────────────────────────────────────────────────────────────────────────
   zai-coding-plan   1,000   4,000,000   7,000,000           0   1,000,000    500,000  12,500,000   $12.00
   openai              184   1,000,000           0           0     184,338      8,002   1,192,340   $12.00
   cerebras             20     432,110     812,904           0      20,000      4,005   1,269,019       —

  ────  By Model  ────

    zai-coding-plan
     Model          Variant  Turns    Input  Cache Read  Cache Write  Output  Reasoning      Total      Cost
     glm-4.7        default   1,000  4.00M      7.00M           0    1.00M       500K    12.50M   $12.00
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

### Windows PowerShell

Install with [pipx](https://pipx.pypa.io/), which keeps `oc-usage` isolated from
your other Python projects. In PowerShell:

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
# Open a new PowerShell window after ensurepath updates PATH.
pipx install git+https://github.com/imluckii/oc-usage.git
```

If `oc-usage` is not found after restarting PowerShell, check the executable
directory pipx selected and add that directory to your user PATH:

```powershell
pipx environment --value PIPX_BIN_DIR
pipx list
Get-Command oc-usage -ErrorAction SilentlyContinue
```

After changing PATH, start a new PowerShell window (or refresh the current
process's `$env:Path`). Upgrade or remove the installation with:

```powershell
pipx upgrade oc-usage
pipx uninstall oc-usage
```

Windows database paths can be passed directly. Quote paths containing spaces,
`#`, `?`, `%`, or non-ASCII characters:

```powershell
oc-usage --db "$env:LOCALAPPDATA\opencode\opencode-next.db"
oc-usage --db "$env:LOCALAPPDATA\opencode\opencode.db"
oc-usage --db 'C:\Users\Ada Lovelace\AppData\Local\opencode\opencode-next.db'
```

OpenCode Desktop's Electron `userData` directory stores application settings and
service metadata, not the authoritative session database. `oc-usage` therefore
never scans Electron settings, logs, or service metadata for databases or
credentials. The embedded/local OpenCode core server uses xdg-basedir data
locations instead.

On Windows, automatic discovery checks these directories in order:

1. `$env:XDG_DATA_HOME\opencode\` (when `XDG_DATA_HOME` is set)
2. `$HOME\.local\share\opencode\` (the xdg-basedir fallback)
3. `$env:LOCALAPPDATA\opencode\` (compatibility fallback)
4. `$HOME\AppData\Local\opencode\` (compatibility fallback)

The same current XDG-first rule applies on Linux and macOS: use
`${XDG_DATA_HOME:-~/.local/share}/opencode/`. macOS's older
`~/Library/Application Support/opencode/` is checked only after the XDG path.
In each directory, filename order is deterministic:
`opencode-next.db`, `opencode.db`, `opencode-dev.db`, then
`opencode-local.db`. These correspond to `@next`/`opencode2`, stable/latest/
beta/prod, dev, and local channels. `oc-usage` selects exactly one database and
never merges or double-counts files or directories. Directory precedence is
evaluated only after filename precedence, so an `opencode-next.db` in a later
compatibility directory wins over an `opencode.db` in an earlier one.

If several files exist, inspect their timestamps and use an explicit path when
you know which channel is active:

```powershell
Get-ChildItem `
  "$env:XDG_DATA_HOME\opencode\opencode*.db", `
  "$HOME\.local\share\opencode\opencode*.db", `
  "$env:LOCALAPPDATA\opencode\opencode*.db", `
  "$HOME\AppData\Local\opencode\opencode*.db" `
  -ErrorAction SilentlyContinue |
  Select-Object FullName, Length, LastWriteTime

oc-usage --db "$env:LOCALAPPDATA\opencode\opencode.db"
```

Use `--db` when OpenCode is configured to keep its data elsewhere. `--db` is the
highest-precedence source selector. Alternatively, `OPENCODE_DB` selects one
database before automatic discovery; an absolute value is used as-is and a
relative value is resolved under the current XDG OpenCode data directory:

```bash
OPENCODE_DB=opencode-dev.db oc-usage --json
OPENCODE_DB=/tmp/synthetic-opencode.db oc-usage
```

`:memory:` cannot be read after the OpenCode process exits and is rejected with
a clear error. Electron in-memory service credentials are never extracted from
logs or settings.

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
usage: oc-usage [-h] [--db PATH | --server URL] [--username NAME]
                [--password-env NAME | --password-stdin] [--full] [--no-color]
                [--plain] [--ascii] [--json] [--version]

All-time OpenCode token usage from a local session database or one OpenCode V2
HTTP server. Reads assistant turns and reports tokens by provider, model, and
variant, with cost when tracked.

options:
  --db PATH        path to one opencode .db file (highest source precedence; never merged)
  --server URL     read one OpenCode V2 HTTP server (mutually exclusive with --db)
  --username NAME  HTTP Basic username for --server (default: opencode)
  --password-env NAME
                   read HTTP Basic password from environment variable NAME
  --password-stdin
                   read HTTP Basic password from one line of stdin
  --full           show full integers (no K/M rounding)
  --no-color       disable ANSI colors
  --plain          alias for --no-color
  --ascii          use ASCII-only boxes and progress bars (auto-enabled for legacy encodings)
  --json           emit JSON to stdout
  --version        show version and exit
  -h, --help       show this help message and exit
```

### Selecting a database (`--db`)

By default `oc-usage` auto-detects one database, preferring the active **v2**
filename and then the other channel filenames in the documented order. To
inspect a specific one:

```bash
# v2 (newer / opencode-next)
oc-usage --db ~/.local/share/opencode/opencode-next.db

# v1 (legacy)
oc-usage --db ~/.local/share/opencode/opencode.db
```

For a redirected report or a legacy Windows console that cannot render Rich's
Unicode box drawing, request the portable form explicitly:

```powershell
oc-usage --ascii
oc-usage --ascii --no-color > usage.txt
```

Unicode Rich output remains the default for UTF-8 terminals, including modern
Windows Terminal and PowerShell. If stdout declares a non-UTF-8 encoding,
`oc-usage` automatically switches to the ASCII renderer. If a stream still
rejects Unicode, it retries once in ASCII; labels that contain non-ASCII data
are escaped rather than causing a crash. `--json` is unchanged and remains
machine-readable, while `--plain` / `--no-color` only control styling.

> ⚠️ **Don't point two invocations at different databases and add them up.**
> After migrating, history can be **duplicated** across `opencode.db` and
> `opencode-next.db`. `oc-usage` deliberately reads **one** database per run.
> Pick the one that holds your current history (usually v2).

### Desktop embedded service, remote servers, and WSL (`--server`)

For Desktop's embedded service, local DB mode is usually the most complete and
private option when the XDG data directory is accessible. Use server mode when
the database is inaccessible (for example, Windows WSL or a remote Desktop
server) but the OpenCode V2 HTTP API is reachable:

```bash
# Password is read without appearing in shell history or process arguments.
printf '%s\n' "$OPENCODE_SERVER_PASSWORD" | \
  oc-usage --server http://127.0.0.1:4096 --password-stdin

# Or read it from a named environment variable.
export OPENCODE_SERVER_PASSWORD='use-a-secret-manager-in-real-usage'
oc-usage --server https://opencode.example.test --password-env OPENCODE_SERVER_PASSWORD
```

`4096` above is only an example/configured server address, not a universal
Desktop default. `--server` and `--db` are mutually exclusive: server mode
reads one server only and never implicitly aggregates it with local history.
`--username` defaults to `opencode`; there is deliberately no plaintext
`--password` option. `--password-env` and `--password-stdin` are mutually
exclusive and are rejected unless `--server` is supplied.

The client implements the current V2 contract documented at
[`/v2/docs/api`](https://opencode.ai/v2/docs/api) and
[`/v2/openapi.json`](https://opencode.ai/v2/openapi.json): it lists sessions
with `GET /api/session`, follows `cursor.next`, then lists projected messages
with `GET /api/session/{sessionID}/message`, follows that endpoint's cursor,
and counts only `type: "assistant"` messages. It uses each assistant message's
`model.id`, `model.providerID`, optional `model.variant`, `tokens` (including
`cache.read` and `cache.write`), `cost`, and `time.created`. It does not use
session-level aggregate totals. Incompatible JSON, HTTP 401/403, timeouts,
connection failures, and unreachable/authenticated servers fail loudly rather
than being reported as zero.

The report header identifies `local database` or `server`; JSON adds a safe
`source` object. Passwords are never printed or included in source metadata.
Remote mode is read-only from this tool's perspective, but server access still
depends on network reachability, API version, permissions, and the server's
configured authentication. No attempt is made to find Desktop credentials in
Electron settings or logs.

### Inside OpenCode

From the interactive OpenCode TUI shell mode, run it inline (this is the bare
shell form):

```
!oc-usage
```

This inline form works in the OpenCode v2 TUI as long as `oc-usage` is on the
shell's PATH. On Windows, install it with pipx as above and verify with
`Get-Command oc-usage` before starting OpenCode.

#### Optional global `/stats` command

To turn `oc-usage` into a named slash command, add an OpenCode v2 command file.
The official global location is `~/.config/opencode/commands/` (or use your
project's `.opencode/commands/`). On Windows, the corresponding user path is
normally `$HOME\.config\opencode\commands\`. Create `stats.md` there:

```markdown
---
description: All-time OpenCode token usage
---

!`oc-usage`
```

The backtick-wrapped interpolation in the code block is OpenCode's
stored-command syntax; the bare `!oc-usage` form above is only for interactive
TUI shell mode. Then `/stats` runs `oc-usage` for you. On Windows, make sure
`oc-usage` is on the PATH inherited by OpenCode (for example, verify it with
`Get-Command oc-usage` in the same environment).

The command file is optional and does not change database discovery. If your
OpenCode v2 build uses a custom command directory, follow that build's
configuration, but retain the same backtick-wrapped interpolation form in the
file.

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
| Linux/Unix | `${XDG_DATA_HOME:-~/.local/share}/opencode/...` |
| macOS      | `${XDG_DATA_HOME:-~/.local/share}/opencode/...`, then `~/Library/Application Support/opencode/...` (compatibility) |
| Windows    | `%XDG_DATA_HOME%\opencode\...` when set; then `%USERPROFILE%\.local\share\opencode\...`; then `%LOCALAPPDATA%\opencode\...`; then `%USERPROFILE%\AppData\Local\opencode\...` |

Auto-detection checks directories in the listed order and filenames in the
channel order documented above. It returns one database only; override with
`--db` any time, especially when multiple channel files are present.

---

## Privacy & read-only guarantee

- The database is opened with a SQLite `mode=ro` URI. `oc-usage` **cannot**
  modify your data and creates no sidecar files from reading.
- It reads **only** the columns it needs to sum tokens/cost (`data`, `type`).
- Local mode transmits nothing and makes no network calls. Server mode makes
  only the GET requests needed for the selected server's session/message API;
  it does not upload prompts or modify server state.
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
- **Desktop/server access.** Local Desktop history may be inaccessible when its
  XDG directory is on another machine, inside WSL, or protected by OS
  permissions. Server mode requires a reachable V2 API and credentials accepted
  by that server; it cannot bypass network, firewall, proxy, or API-version
  restrictions.
- **No automatic merge or dedupe across sources.** A run has exactly one local
  database or one server source. Pagination protects against repeated message
  IDs within a broken server response, but histories from separate sources are
  never combined.

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
├── remote.py     # standard-library V2 HTTP session/message client
├── models.py     # normalized rows, buckets, aggregation
└── render.py     # Rich report + JSON output
```

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Released under the
[MIT License](LICENSE).
