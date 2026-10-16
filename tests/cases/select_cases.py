"""Changed-file selection regressions; never run the real full suite."""
from contextlib import nullcontext, redirect_stderr, redirect_stdout
import io
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import select_cases as selector
from support import check


def selection_root_dependencies():
    roots = {"tokens": "", "seats_config": "", "ai_worker": "import seats_config",
             "route": "from seats_config import load_seats",
             "panel": "import ai_worker", "unrelated": "import os"}
    cases = {"weekly_tokens": "python3 tokens.py", "worker": "import ai_worker as w",
             "routes": "from route import main", "panel": "python3 panel.py",
             "unrelated": "python3 unrelated.py"}
    result = selector.select_modules(["tokens.py"], roots, cases)
    check(result.reasons == {"weekly_tokens": ["tokens.py"]} and not result.fallback,
          str(result))
    result = selector.select_modules(["seats_config.py"], roots, cases)
    check(set(result.reasons) == {"worker", "routes", "panel"} and not result.fallback,
          str(result))
    check(selector.references_module("import os, tokens as t", "tokens"), "多模块 import")
    check(not selector.references_module("import tokens_extra", "tokens"), "模块名边界")
    result = selector.select_modules(["deleted.py"], {"caller": "import deleted"},
                                     {"caller": "python3 caller.py"})
    check(result.reasons == {"caller": ["deleted.py"]}, str(result))


def selection_fallbacks_and_case_files():
    cases = {"one": "", "two": ""}
    for path in selector.FULL_SUITE_FILES:
        result = selector.select_modules([path], {}, cases)
        check(result.fallback and set(result.reasons) == set(cases), str(result))
    result = selector.select_modules(["foo.toml"], {}, cases)
    check(result.fallback and "foo.toml" in result.fallback, str(result))
    result = selector.select_modules(["tests/cases/one.py"], {}, cases)
    check(result.reasons == {"one": ["tests/cases/one.py"]} and not result.fallback,
          str(result))
    result = selector.select_modules(["tokens.py", "foo.toml"], {"tokens": ""},
                                     {"one": "tokens.py"})
    check(result.fallback, "每个未匹配非文档文件都必须兜底")
    result = selector.select_modules(["tokens.py"], {"tokens": "broken ("}, cases)
    check(result.fallback and "AST" in result.fallback, str(result))


def selection_documents_and_known_paths():
    cases = {"readme": "README.md", "install": "install.sh uninstall.sh bin/aiw seats.json router-rules.json"}
    for path in ("docs/x.md", "assets/x.png", "LICENSE", ".github/x.yml", ".coderabbit.yaml"):
        result = selector.select_modules([path], {}, cases)
        check(not result.reasons and not result.fallback, str(result))
    result = selector.select_modules(["README.md"], {}, cases)
    check(result.reasons == {"readme": ["README.md"]} and not result.fallback, str(result))
    for path in ("bin/aiw", "install.sh", "uninstall.sh", "seats.json", "router-rules.json"):
        result = selector.select_modules([path], {}, cases)
        check(result.reasons == {"install": [path]} and not result.fallback, str(result))


def selection_git_union_and_bases():
    def output(root, *args):
        if args[0] == "rev-parse":
            if args[-1].startswith("origin/main"):
                raise subprocess.CalledProcessError(1, args)
            return "hash"
        if args[0] == "ls-files":
            return "new file.py\0"
        if "--cached" in args:
            return "staged.py\0shared.py\0"
        if args[-1].endswith("...HEAD"):
            return "committed.py\0shared.py\0"
        return "dirty.py\0"
    with patch.object(selector, "git_output", side_effect=output) as git:
        paths, fallback = selector.changed_files("unused")
        check(paths == ["committed.py", "dirty.py", "new file.py", "shared.py", "staged.py"]
              and not fallback, str((paths, fallback)))
        check(any(call.args[-1] == "main...HEAD" for call in git.call_args_list), "应尝试 main")
    with patch.object(selector, "git_output", side_effect=output) as git:
        selector.changed_files("unused", "HEAD~1")
        check(not any(call.args[1] == "rev-parse" for call in git.call_args_list), "显式 BASE")
    with patch.object(selector, "git_output", side_effect=subprocess.CalledProcessError(1, "git")):
        check(selector.changed_files("unused")[1] is not None, "无默认基线应兜底")


def selection_cli_conflicts_and_empty():
    harness = sys.modules.get("test_harness", sys.modules["__main__"])
    case = next(iter(harness.CASES))
    with redirect_stderr(io.StringIO()) as error, redirect_stdout(io.StringIO()), \
            patch.object(harness, "run_named") as run, \
            patch.object(harness, "run_parallel") as parallel, \
            patch.object(harness, "suite_reservation") as reserve:
        for argv in (["--changed", "HEAD", case], [case, "--changed"], ["--changed", case]):
            check(harness.main(argv) == 64, str(argv))
        with patch.object(harness, "changed_files", return_value=([], None)):
            check(harness.main(["--changed"]) == 0, "无改动应成功")
        check("无改动，不运行" in error.getvalue(), error.getvalue())
        run.assert_not_called()
        parallel.assert_not_called()
        reserve.assert_not_called()


def selection_cli_modules_and_slots():
    harness = sys.modules.get("test_harness", sys.modules["__main__"])
    groups = {"small": ("a",), "large": ("b", "c", "d"), "other": ("e", "f")}
    with patch.object(harness, "MODULE_CASES", groups), \
            patch.object(harness, "CASE_MODULES", tuple(groups)), \
            patch.object(harness, "CASES", dict.fromkeys("abcdef")), \
            patch.object(harness, "changed_files", return_value=(["example.py"], None)), \
            patch.object(harness, "suite_reservation", return_value=nullcontext()) as reserve, \
            patch.object(harness.slot, "thread_environment", return_value={}) as threads, \
            patch.object(harness.slot, "qos"), patch.object(harness, "lower_priority"), \
            patch.object(harness, "run_named", return_value=0) as run, \
            redirect_stderr(io.StringIO()) as error, redirect_stdout(io.StringIO()):
        for module, queued in (("small", False), ("large", True)):
            with patch.object(harness, "select_from_repo", return_value=selector.Selection(
                    {module: ["example.py"]})):
                check(harness.main(["--changed", "HEAD", "-j", "1"]) == 0, module)
            check(run.call_args.args[0] == list(groups[module]), "选中模块须运行全部注册用例")
            check(reserve.call_args.args[0] == ([] if queued else list(groups[module])), "名额阈值")
        threads.assert_called_once()
        with patch.object(harness, "select_from_repo", return_value=selector.Selection({}, "兜底")):
            check(harness.main(["--changed", "HEAD", "-j", "1"]) == 0, "全量兜底")
        check(run.call_args.args[0] == list("abcdef") and reserve.call_args.args[0] == [], "原有全量路径")
        check("large ← example.py" in error.getvalue() and "用例数：3 / 6" in error.getvalue(), error.getvalue())


def selection_slow_reports():
    harness = sys.modules.get("test_harness", sys.modules["__main__"])
    def run(names, durations):
        durations.extend((f"slow_{i}", float(i)) for i in range(20))
        return 0
    with patch.object(harness, "run_named", side_effect=run), \
            patch.object(harness, "lower_priority"), redirect_stdout(io.StringIO()) as output:
        check(harness.main(["--slow", "5", "--durations", next(iter(harness.CASES))]) == 0, "状态")
    durations, slow = output.getvalue().split("Slow cases (> 5s; up to 10):")
    check(len(durations.splitlines()) == 16, "--durations 保持 15 项")
    check(len(slow.strip().splitlines()) == 10 and "slow_19" in slow and "slow_9" not in slow, slow)


def selection_real_repo_unknown_file():
    harness = sys.modules.get("test_harness", sys.modules["__main__"])
    modules = {}
    for name, case in harness.CASES.items():
        modules.setdefault(case.__module__.rsplit(".", 1)[-1], []).append(name)
    root = Path(harness.__file__).resolve().parents[1]
    import select_cases
    unknown = select_cases.select_from_repo(root, ["foo.toml"], modules)
    check(unknown.fallback is not None, f"未知文件须退回全量：{unknown}")
    uninstall = select_cases.select_from_repo(root, ["uninstall.sh"], modules)
    check("install_doctor" in uninstall.reasons and not uninstall.fallback, str(uninstall))
    own = select_cases.select_from_repo(root, ["tokens.py"], modules)
    check("select_cases" not in own.reasons and "weekly_tokens" in own.reasons, own.reasons)


CASES = {case.__name__: case for case in (
    selection_root_dependencies,
    selection_fallbacks_and_case_files,
    selection_documents_and_known_paths,
    selection_git_union_and_bases,
    selection_cli_conflicts_and_empty,
    selection_cli_modules_and_slots,
    selection_slow_reports,
    selection_real_repo_unknown_file,
)}
