# aiw usage guide

Start with the [README](../README.en.md) to see what problems it solves; this guide covers installation details, how it works and customization.

## What the installer changes

The installer makes these changes:

- `~/.claude/CLAUDE.md`: adds the aiw import, then existing personal rules; keeps other text.
- `~/.claude/settings.json`: adds/refreshes the `UserPromptSubmit` hook; keeps other settings/hooks.
- `~/.local/bin/aiw`: creates or replaces a symlink to this checkout, **without a backup**.
- `~/.zshrc`: adds the marked PATH block only if `~/.local/bin` is missing from PATH and the marker is absent.
- If `~/.codex` exists or `codex` is on PATH: updates the aiw block in `~/.codex/AGENTS.md` and the hook in `~/.codex/hooks.json`. Keeps text outside the block. Set `AIW_NO_CODEX=1` to skip this.
- Creates `state/`, makes entry scripts executable, checks routing and runs doctor; warnings may not fail installation.

Changed existing files get `.bak-YYYYMMDD-HHMMSS` backups; new/unchanged files do not. JSON is checked before edits. Reinstalling avoids duplicates. `--claude-hook=plugin` removes the settings hook; manage plugin hooks separately. It does not install a plugin.

## How it works

aiw shares work from Claude Code or Codex: code goes to Codex, reviews to another model, drafts and summaries to optional cheap/free models. The main session plans/checks results; Codex also edits code. A **seat** is a model or CLI that takes assignments. Fallback tries another seat; circuit breakers temporarily skip unavailable seats. Not a framework or service: rule files and standard-library Python scripts.

`codex`/`codex-sol` need Codex CLI; `claude` needs Claude Code. A CLI runs in a terminal.

- `aiw`: assign work.
- Dispatch hooks: suggest seats inside Claude Code/Codex; do not assign automatically.
- `aiw panel`: independent opinions (a “panel”). Exit code 0 requires at least two successful seats with output (`rc == 0` and `chars > 0`); fewer return 1. A single seat still runs and its result is displayed, but its opinion does not constitute a panel.
- `aiw goal`: a task list with executable checks.
- An improvement buffer: observations for later root-cause review.
- `aiw doctor`: installation/configuration checks.

```text
Main session → dispatch hook suggestion → main session runs aiw → seat
                                                               ↓ failure
                                               next configured fallback
                                               / temporary circuit breaker
Result → main session checks files, evidence and tests
```

All output is a **draft for review**. On success, stderr reports the actual seat and the actual `provider` file path. The default location is the run directory; setting `AIW_PROVIDER_FILE` writes it to the specified path. Write jobs share a lock across the Git repository root and its subdirectories. Git write jobs save `baseline-status.txt` before execution and report pre-existing changes separately from files with new changes during the job. Defaults contain `codex`, `codex-sol`, and `claude`, with empty fallback chains. Add local chains to enable fallback. Text seats need `--attach file` to see code. For direct jobs, attachments go to the actual seat: CLI seats receive only a validated file list (relative path, byte count, first 12 SHA256 hex digits) and read files in the workdir; other seats receive inline text. Both modes retain the 200KB cap and safety checks. Above 30KB, direct jobs to text seats trigger one warning without rejection. Panel reads attachments once during preflight and appends inline text to the shared `prompt.md` snapshot; every seat, including CLI seats, receives the same full contents. Panel warns once when total attachments exceed 30KB, including panels with only CLI seats. Attachment contents are sent as-is to the model service behind the seat. Safety checks block credential-like files, out-of-tree paths, binaries and similar cases, but do not detect passwords, keys or private data inside file text; check before sending.

## Add more models (optional)

One-command setup:

```bash
~/.claude/aiw/install.sh --with-agent-use
```

This clones or fast-forwards the public core and [agent-use-adapters](https://github.com/Jerryx333/agent-use-adapters), and runs the adapter installer. Only a successful adapter install (including `Already installed` with exit code 0) adds the missing local `opencode` free text seat and empty fallback entry; existing seats are preserved. Adapter clone or install failures (such as registry conflicts) warn and skip seat additions while the overall install continues and the existing registry is preserved. If the active registry already has an `opencode` provider, add a seat manually using the next section. Install and log in to the CLI separately as required. Combine this option with `--claude-hook=plugin` if needed.

The core defaults to `~/code/ai/agent-use` (`AGENT_USE_HOME` overrides it); adapters default to the sibling `agent-use-adapters` (`AIW_ADAPTERS_HOME` overrides it). Seats go into `AIW_CONFIG` or `~/.config/aiw/seats.local.json`. Symlinks are preserved: backups and temporary files go in the real target directory, and the target is replaced atomically. Dangling symlinks are left unchanged with a warning. `aiw sync` also fast-forwards both installed Git repositories; their failures only warn. `free_only` labels the seat in doctor; `free_guard` disables a seat or model on evidence of charges. Free model selection is handled by the adapter; OpenCode model-level disable records currently do not filter dispatcher default/fallback models.

For manual setup, follow these steps:

Use the public [agent-use core](https://github.com/Jerryx333/agent-use). Set `AGENT_USE_HOME` to its checkout if it is not at `~/code/ai/agent-use`.

1. Register a **provider** (a named adapter that calls a model/tool) in `~/.config/agent-use/dispatcher.json`. A simple starting point is agent-use's `examples/cli_adapter.py`, to wrap a CLI. Follow its registry example: task text goes to stdin, stdout becomes the response. Install/login separately.
2. Add a seat to `~/.config/aiw/seats.local.json`, replace provider/model placeholders:

   ```json
   {
     "seats": {
       "local-text": {
         "kind": "agent_use",
         "provider": "your-provider",
         "model": "your-model"
       }
     },
     "fallback": {"local-text": []}
   }
   ```

3. Optionally route drafting tasks there using `router-rules.local.json`; see [the seat example](../seats.local.example.json) and [routing example](../router-rules.local.example.json).
4. Run `aiw doctor`. It warns if an enabled seat's provider is missing or disabled in the active registry; these warnings do not change its exit code.

## Customize

Local files live under `~/.config/aiw/`:

| File | Controls |
| --- | --- |
| `seats.local.json` | Seats, models, fallback chains, panel lists and other local settings. |
| `router-rules.local.json` | Which task descriptions suggest which seats. |
| `CLAUDE.personal.md` | Personal instructions imported after public Claude rules. |
| `AGENTS.personal.md` | Personal instructions copied after public Codex rules during installation. |

Personal rules override conflicts. Rerun `install.sh` after adding personal files or changing Codex personal rules. Project routing goes in `.claude/router-rules.json`: project > local > public. Seat configuration recursively merges objects; lists replace whole lists.

Codex/Claude seats use your own accounts and their billing. `free_only` labels a seat as free-only and declares that it should not switch to paid models; automatic disabling requires `free_guard`, which disables a provider/model when reported charging is detected. These fields, along with `ro_only`, `require_no_tools` and `expects_credits`, must be booleans when present. This is detection after a call, not a guarantee of zero cost.

## Multiple machines

Clone the same public repository on each machine. Keep settings/rules in your own private repository; symlink into `~/.config/aiw/`. Compare `aiw doctor --fingerprint` on both; login and runtime state stay local.

## Uninstall

No uninstall option. Undo these changes; keep unrelated settings:

- Remove the aiw import and its personal-rule import from `~/.claude/CLAUDE.md`.
- Remove only hooks whose command contains `aiw/route.py` from `~/.claude/settings.json` and, if installed, `~/.codex/hooks.json`; keep valid JSON. Remove the `aiw:begin` through `aiw:end` block from `~/.codex/AGENTS.md`.
- Remove the `~/.local/bin/aiw` symlink and the `# aiw: on PATH` block from `~/.zshrc` if the installer added it.
- Stop any separately installed plugin hook. Delete the checkout (including local `state/`) and local aiw settings only when no longer needed. Restore `.bak-*` files only if no later changes would be lost; the symlink has no backup.
