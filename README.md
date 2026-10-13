# ai-harness

[English](README.md) | [中文](README.zh-CN.md)

Let your main Claude Code / Codex model focus on planning and checking results. Hand off routine work such as reading code, running commands and drafting summaries to Codex or cheaper models you already have available. Install it and start using it, with no service to run or dependencies to install.

## What problems does it solve?

- Your main model's allowance runs out: subscriptions have usage windows and weekly limits, so work can stop halfway through. Reading large files, digging through logs and running tests use up that allowance.
- Your context fills up: when the main conversation fills with file contents and command output, it gets compacted, loses details and makes mistakes.
- Your other subscriptions and free models sit idle: you have a Codex/ChatGPT subscription or free models available, but only one main conversation is doing the work.

## What changes after installation?

- Your main model's allowance goes further: hand off routine work and spend it on planning and checking results.
- The main conversation stays cleaner: assigned work reads files and runs commands elsewhere; the main conversation receives the results.
- You get another review: different models can write and review code; `aiw panel` collects independent opinions from several models.
- Keep your usual workflow: use Claude Code or Codex as usual. When work is suitable to hand off, it suggests who should take it; the main conversation decides whether to assign it.
- If a model is unavailable, it can automatically try the next one so work can continue. By default there is only one choice per task; set the alternatives and their order in [local configuration](docs/guide.md#customize).

## Want to connect more models? Use agent-use

If you have other subscriptions, local models or free models and want them to share the work, common problems are:

- **Each one works differently**, so every new connection takes its own setup. With [agent-use](https://github.com/Jerryx333/agent-use), any model you can use in a terminal connects in the same way. Add a few configuration lines; no code needed.
- **An outage or rate limit stops the work.** agent-use automatically tries the next model in your chosen order so work can continue.
- **Can you trust what another model returns?** Original responses and execution records are saved every time. All results are drafts for the main conversation to check before accepting them.

See the [setup steps](docs/guide.md#add-more-models-optional).

## Questions you may have

**Will quality suffer?** All assigned work is a draft for review. The main conversation checks files, evidence and test results before accepting it.

**Does assigning work also use the main model's tokens?** Yes: writing instructions, reading results and checking them all use tokens. Very small tasks may not be worth handing off, so it only suggests assignments; it does not make them automatically. To see your actual usage, use `aiw tokens` (local Claude Code main-session usage and context breakdown) and `aiw weekly` (assignment success rates and token totals).

**Does it cost money?** Codex and Claude use your own accounts and their billing. Connected free models can be marked for free-tier use only and automatically disabled when charging is detected. Detection happens after a call, so this does not guarantee zero cost.

**Where does my code go?** Task instructions and attached material go to the service behind the model you assign. Models that only handle text cannot see your code by default; you must explicitly attach it. Do not send passwords, keys or private data to third-party models.

**Only Claude Code, no Codex?** Installation works, but the default model for code changes (`codex`) and read-only reviews (`codex-sol`) require Codex CLI. This limits the default Claude-led workflow.

**Linux?** Untested. Only macOS has been tested.

**Are the rules too forceful?** Override them with personal rules, or remove `@~/.claude/ai-harness/CLAUDE.md` from `~/.claude/CLAUDE.md`. To stop assignment suggestions too, remove the harness hooks as described in [Uninstall](docs/guide.md#uninstall).

## Five-minute start

You need macOS (the only tested OS), Python 3, and logged-in Claude Code and/or Codex CLI (terminal programs). Optional: [agent-use](https://github.com/Jerryx333/agent-use) to connect more models.

1. Clone and install at this exact location:

   ```bash
   git clone https://github.com/Jerryx333/ai-harness.git ~/.claude/ai-harness
   ~/.claude/ai-harness/install.sh
   ```

   See the [guide](docs/guide.md#what-the-installer-changes) for changed files and backups.

2. Use each CLI's official login: `codex login`, or run `claude` and enter `/login`. Open a new terminal if PATH changed, then run:

   ```bash
   aiw doctor
   ```

   Configuration warnings do not change doctor's exit code. Codex may ask you to trust the new hook when it next starts.

3. Try a read-only review of this checkout (requires Codex):

   ```bash
   printf '%s\n' 'Read this repository. Explain its entry points with file references. Do not edit files.' > /tmp/aiw-review.txt
   aiw codex-sol "$HOME/.claude/ai-harness" 900 /tmp/aiw-review.txt --ro
   ```

4. Use Claude Code or Codex as usual. Hooks suggest assignments; the main session plans and verifies.

## Common commands

`.` is your project; `task.txt` holds instructions; timeouts are seconds. Prepare input files first.

| Command | Purpose and example |
| --- | --- |
| `aiw <seat> <dir> <seconds> <prompt> [--ro] [--net]` | Assign work; `--net` enables network and local ports for Codex write tasks only (default off); conflicts with `--ro` and non-Codex seats return 64. `--ro` requests read-only: `aiw codex-sol . 900 task.txt --ro`. |
| `aiw panel` | Opinions: `aiw panel . 600 task.txt --prior prior.md`; defaults to at most 4 items and 1500 characters, each with conclusion, evidence (or “无证据”), and impact or advice; stdout includes answers (up to 3000 characters per seat, with full archives). `--full` adds no format requirement and omits each seat's answer from stdout (full answers are in `<seat>.md` in the panel directory); automatically detects the main session in Claude Code; for a Codex main session, add the `AIW_MAIN=codex` prefix; excludes the main session, possibly leaving one reviewer. Write your judgment first and pass it with `--prior`; then adjudicate each key opinion: accept / reject (with reasons) / defer (state what is pending), without majority voting. |
| `aiw map` | Apply one instruction per file: `aiw map . 600 task.txt src/a.py src/b.py`; defaults to `codex-sol`, up to 3 jobs. |
| `aiw goal init / verify` | Create a checklist with `aiw goal init docs/goals/demo.json`; edit its example tasks, `verify` commands and `paths`, then run `aiw goal verify docs/goals/demo.json --full`. |
| `aiw doctor` | Check seats and setup: `aiw doctor`; `aiw doctor --fingerprint` prints a fingerprint (version/hash summary). |
| `aiw note` | Save an observation: `aiw note . "Check failed" --expected "Check passes" --evidence "test output"`. |
| `aiw sync` | Update this harness checkout using `git pull --ff-only`: `aiw sync`; cannot merge divergent history. Rerun the installer to refresh installed rules/hooks. |
| `aiw gc` | Delete old run/panel/map archives (30 days by default); preview first: `aiw gc 30 --dry-run`. |

## More documentation

- [Usage guide](docs/guide.md): installation details, how it works and customization.
- [Reference manual](docs/reference.zh-CN.md) (Chinese): all commands and detailed rules.
- [Improvement buffer](docs/findings.md)
- [State contract](docs/state-contract.md)
- [Invariants and regression checks](docs/invariants.md)

## Contributing

Run `python3 tests/test_harness.py` (background priority; concurrency defaults to one quarter of CPUs (minimum 1); `-j N` is capped at half of CPUs (minimum 1), `-j 1` runs serially, `--durations` lists the slowest cases) and `python3 tests/mutation_check.py`, then open a PR. Cases live in `tests/cases/`, with shared helpers in `tests/support.py`.

License: [Apache-2.0](LICENSE).
