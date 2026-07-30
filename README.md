# oc-usage

> All-time **OpenCode** token usage, straight from your running OpenCode V2 service.

`oc-usage` reads every assistant message from your running OpenCode V2 service
and reports token usage — broken down by **provider**, **model**, and
**variant** — with **cost** when it is tracked. Every number is summed from
recorded per-message components, so it stays exact even for sessions that switch
model mid-stream.

- 🧮 **Exact** — summed from each message's `input`, `cache.read`, `cache.write`,
  `output`, and `reasoning`. No estimates, no cache.
- 💸 **Honest about cost** — shown only when OpenCode actually recorded it.
- 🎨 **Beautiful by default** (Rich), with `--json` for scripts.
- 🔒 **Read-only** — it only asks OpenCode for data; it never writes anywhere.
- 📦 **One dependency:** [`rich`](https://github.com/Textualize/rich).

---

## Requirements

OpenCode **V2** (`opencode2`). `oc-usage` talks to OpenCode through its built-in
`api` command, so OpenCode owns service discovery, startup, and authentication
and always reflects your currently active server. Install OpenCode V2 per its
[docs](https://opencode.ai/v2/docs/) and make sure `opencode2` (or `opencode`)
is on your `PATH`.

---

## Install

Requires Python **3.10+**. Recommended: install isolated with **pipx**.

```bash
pipx install git+https://github.com/imluckii/oc-usage.git
pipx upgrade oc-usage      # later
pipx uninstall oc-usage    # remove
```

<details><summary>Windows</summary>

Install [pipx](https://pipx.pypa.io/), then `oc-usage` in PowerShell:

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
# open a new PowerShell window, then:
pipx install git+https://github.com/imluckii/oc-usage.git
```

If `oc-usage` is not found after restarting PowerShell, find pipx's bin dir and
add it to your user `PATH`: `pipx environment --value PIPX_BIN_DIR`.

</details>

---

## Usage

```bash
oc-usage          # show the report
oc-usage --json   # machine-readable JSON
```

That is the whole interface — `usage: oc-usage [-h] [--json] [--version]`. The
default report renders compact numbers; `--json` carries exact values (totals,
providers, models, span).

### Desktop & TUI

`oc-usage` reads from the OpenCode service your shell is connected to. From
OpenCode's TUI **shell mode**, run it inline: `!oc-usage`.

> OpenCode Desktop runs its own private service. If `oc-usage` cannot see it, run
> it from the same environment Desktop launched. `oc-usage` never searches
> Desktop files or credentials.

---

## What it counts

Each figure comes from a single **assistant message**, which carries its own
`model`, `tokens`, `cost`, and `time`. `oc-usage` lists every session
(`GET /api/session`) and every message in it (`GET /api/session/{id}/message`),
following the service's opaque pagination cursor in both cases, keeps only
`type == "assistant"` messages, deduplicates IDs across pages, and sums:

| Category      | Source                             |
| ------------- | ---------------------------------- |
| `input`       | `tokens.input`                     |
| `cache_read`  | `tokens.cache.read`                |
| `cache_write` | `tokens.cache.write`               |
| `output`      | `tokens.output`                    |
| `reasoning`   | `tokens.reasoning`                 |
| `total`       | sum of the five components         |
| `cost`        | sum of recorded per-message `cost` |

Cost is **never estimated**. Session-level aggregate totals are not used, so a
session that changed model/provider/variant still attributes each turn exactly.

---

## Privacy & read-only behavior

- `oc-usage` only sends OpenCode `api get` requests (read-only GETs). It never
  writes, uploads, or modifies anything.
- It reads only the fields it needs to sum tokens and cost. Message **content**
  (prompts, reasoning, tool output) is never parsed, stored, or printed — only
  aggregate counts appear in reports.
- No database paths, socket addresses, or credentials are required or exposed.

---

## Troubleshooting

- **`OpenCode V2 was not found`** — install OpenCode V2; ensure `opencode2` (or
  `opencode`) is on `PATH`.
- **`does not provide the OpenCode V2 api command`** — an older `opencode` binary
  was found. Install or upgrade to OpenCode V2.
- **`OpenCode service is unavailable`** — start or update OpenCode V2, then retry.
- **`returned invalid JSON` / `did not match the V2 API shape`** — your OpenCode
  is likely too old for this version of `oc-usage`; upgrade OpenCode V2.
- **`No assistant messages found`** — run a session in OpenCode first.

`oc-usage` fails loudly rather than reporting zero. Exit codes: `0` success,
`1` runtime error, `2` bad arguments.

---

## Development

```bash
git clone https://github.com/imluckii/oc-usage.git && cd oc-usage
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest -q && python -m build
```

```
src/oc_usage/
├── __init__.py   # version
├── __main__.py   # python -m oc_usage
├── cli.py        # argparse + entry point
├── service.py    # OpenCode api subprocess transport (discovery, pagination)
├── models.py     # normalized rows, buckets, aggregation
└── render.py     # Rich report + JSON output
```

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Released under the
[MIT License](LICENSE).
