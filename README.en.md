# aiw

[中文](README.md) | [English](README.en.md)

Let your main Claude Code / Codex model focus on planning and checking results. Hand off routine work such as reading code, running commands and drafting summaries to Codex or cheaper models you already have available. Install it and start using it, with no service to run or dependencies to install. It works best if you use both Claude Code and Codex.

## What problems does it solve?

- Your main model's allowance runs out: subscriptions have usage windows and weekly limits, so work can stop halfway through. Reading large files, digging through logs and running tests use up that allowance.
- Your context fills up: when the main conversation fills with file contents and command output, it gets compacted, loses details and makes mistakes.
- Your other subscriptions and free models sit idle: you have a Codex/ChatGPT subscription or free models available, but only one main conversation is doing the work.

## What changes after installation?

- Your main model's allowance goes further: hand off routine work and spend it on planning and checking results.
- Less clutter in the main conversation: assigned work reads files and runs commands elsewhere. Those file contents and command output stay out of the main conversation; it receives only the results.
- You get another review: different models can write and review code; `aiw panel` collects independent opinions from several models.
- Keep your usual workflow: use Claude Code or Codex as usual. When work is suitable to hand off, it suggests who should take it; the main conversation decides whether to assign it.
- If a model is unavailable, it can automatically try the next one so work can continue. By default there is only one choice per task; set the alternatives and their order in [local configuration](docs/guide.md#customize).

## Want to connect more models? Use agent-use

If you have other subscriptions, local models or free models and want them to share the work, common problems are:

- **Each one works differently**, so every new connection takes its own setup. With [agent-use](https://github.com/Jerryx333/agent-use), any model you can use in a terminal connects in the same way. Add a few configuration lines; no code needed.
- **An outage or rate limit stops the work.** agent-use automatically tries the next model in your chosen order so work can continue.
- **Can you trust what another model returns?** Original responses and execution records are saved every time. All results are drafts for the main conversation to check before accepting them.

Just want common CLIs such as Codex, Claude Code and OpenCode? [agent-use-adapters](https://github.com/Jerryx333/agent-use-adapters) has ready-made adapters. Add one flag when installing aiw to set them up too, including a seat for OpenCode's free models: `~/.claude/aiw/install.sh --with-agent-use`. Manual steps are in the [guide](docs/guide.md#add-more-models-optional).

## Questions you may have

**Will quality suffer?** All assigned work is a draft for review. The main conversation checks files, evidence and test results before accepting it.

**Does assigning work also use the main model's tokens?** Yes: writing instructions, reading results and checking them all use tokens. Very small tasks may not be worth handing off, so it only suggests assignments; it does not make them automatically. To see your actual usage, use `aiw tokens` (local Claude Code main-session usage and context breakdown) and `aiw weekly` (assignment success rates and token totals).

**How long does an assignment take?** It depends on the work. Medians from one week on the author's machine:

- Code changes: about 4 minutes. Codex reads code, edits files and runs tests; it is doing real work.
- Read-only reviews and searching a repository: about 2–3 minutes.
- Text only (drafts, summaries, second opinions): about 20 seconds with free models, about 2 minutes with Claude.

The main conversation can keep working meanwhile. For small, urgent work, doing it yourself is faster.

**Will it mess up my repository?** Code-change work runs in the Codex sandbox: it can write only to the project directory (in a git worktree, also the Git directories `git add` needs) and has no network by default. When it finishes, it lists the changed files. Changes stay in your working tree for the main conversation to review before committing. Read-only work cannot change files.

**Does it cost money?** Codex and Claude use your own accounts and their billing. Connected free models can be marked for free-tier use only and automatically disabled when charging is detected. Detection happens after a call, so this does not guarantee zero cost.

**Where does my code go?** Task instructions and attached material go to the service behind the model you assign. Models that only handle text cannot see your code by default; you must explicitly attach it. Do not send passwords, keys or private data to third-party models.

**Only Claude Code, no Codex?** Installation works, but the default model for code changes (`codex`) and read-only reviews (`codex-sol`) require Codex CLI. This limits the default Claude-led workflow.

**Linux?** Untested. Only macOS has been tested.

**Are the rules too forceful?** Override them with personal rules, or remove `@~/.claude/aiw/CLAUDE.md` from `~/.claude/CLAUDE.md`. To stop assignment suggestions too, remove the aiw hooks as described in [Uninstall](docs/guide.md#uninstall).

## Five-minute start

You need macOS (the only tested OS), Python 3, and logged-in Claude Code and/or Codex CLI (terminal programs). Optional: [agent-use](https://github.com/Jerryx333/agent-use) to connect more models.

1. Clone and install at this exact location:

   ```bash
   git clone https://github.com/Jerryx333/aiw.git ~/.claude/aiw
   ~/.claude/aiw/install.sh
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
   aiw codex-sol "$HOME/.claude/aiw" 900 /tmp/aiw-review.txt --ro
   ```

4. Use Claude Code or Codex as usual. When work is suitable to hand off, it suggests who should take it; the main conversation plans and verifies.


## Common commands

`.` is your project; `task.txt` holds instructions; timeouts are seconds. Prepare input files first.

| Command | Purpose and example |
| --- | --- |
| `aiw <seat> <dir> <seconds> <prompt> [--ro] [--net]` | Assign work: `aiw codex-sol . 900 task.txt --ro`. `--ro` requests read-only; `--net` opens the network for Codex code-change work (off by default). |
| `aiw panel` | Ask several models the same question for independent opinions: `aiw panel . 600 task.txt --prior prior.md`. Pass your own judgment with `--prior`; then accept, reject or defer each opinion, without majority voting. A panel succeeds only when at least two models answer; with one, it still shows the result but returns 1. Output format and options: [reference manual](docs/reference.zh-CN.md#命令) (Chinese). |
| `aiw map` | Apply one instruction per file: `aiw map . 600 task.txt src/a.py src/b.py`; defaults to `codex-sol`, up to 3 jobs. |
| `aiw goal init / verify` | Create a checklist with `aiw goal init docs/goals/demo.json`; edit its example tasks, `verify` commands and `paths`, then run `aiw goal verify docs/goals/demo.json --full`. |
| `aiw doctor` | Check seats and setup: `aiw doctor`; `aiw doctor --fingerprint` prints a fingerprint (version/hash summary). |
| `aiw note` | Save an observation: `aiw note . "Check failed" --expected "Check passes" --evidence "test output"`. |
| `aiw sync` | Update this aiw checkout using `git pull --ff-only`: `aiw sync`; cannot merge divergent history. Installed agent-use and agent-use-adapters checkouts are updated too. Rerun the installer to refresh installed rules/hooks. |
| `aiw gc` | Delete old run/panel/map archives (30 days by default); preview first: `aiw gc 30 --dry-run`. |

## More documentation

- [Usage guide](docs/guide.md): installation details, how it works and customization.
- [Reference manual](docs/reference.zh-CN.md) (Chinese): all commands and detailed rules.
- [Improvement buffer](docs/findings.md)
- [State contract](docs/state-contract.md)
- [Invariants and regression checks](docs/invariants.md)

## Contributing

Run `python3 tests/test_harness.py` and `python3 tests/mutation_check.py` (concurrency and other options: [reference manual](docs/reference.zh-CN.md#自测), Chinese), then open a PR. Cases live in `tests/cases/`, with shared helpers in `tests/support.py`. License: [Apache-2.0](LICENSE).

License: [Apache-2.0](LICENSE).
