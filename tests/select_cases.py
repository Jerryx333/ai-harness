"""Conservative module-level test selection using only source text and Git."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess


FULL_SUITE_FILES = frozenset({
    "tests/support.py", "tests/test_harness.py", "tests/select_cases.py",
    "tests/cases/__init__.py",
})


@dataclass
class Selection:
    reasons: dict[str, list[str]]
    fallback: str | None = None


def git_output(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def changed_files(root, base=None):
    """Return root-relative paths and an optional full-suite fallback reason."""
    if base is None:
        for candidate in ("origin/main", "main"):
            try:
                git_output(root, "rev-parse", "--verify", f"{candidate}^{{commit}}")
            except subprocess.CalledProcessError:
                continue
            except OSError as exc:
                return [], f"无法读取 Git 改动（{exc}）"
            base = candidate
            break
    paths = set()
    try:
        for args in (("diff", "--name-only", "--no-renames", "-z", "--cached"),
                     ("diff", "--name-only", "--no-renames", "-z"),
                     ("ls-files", "--others", "--exclude-standard", "-z")):
            paths.update(filter(None, git_output(root, *args).split("\0")))
        if base is None:
            return sorted(paths), "origin/main、main 均不存在"
        paths.update(filter(None, git_output(
            root, "diff", "--name-only", "--no-renames", "-z", f"{base}...HEAD"
        ).split("\0")))
    except (OSError, subprocess.CalledProcessError) as exc:
        return sorted(paths), f"无法读取 Git 改动（{exc}）"
    return sorted(paths), None


def is_document(path):
    return (path.endswith(".md") or path.startswith(("docs/", "assets/", ".github/"))
            or path in {"LICENSE", ".coderabbit.yaml"})


def references_module(text, name):
    identifier = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*"
    preceding = rf"(?:{identifier}(?:\s+as\s+[A-Za-z_]\w*)?\s*,\s*)*"
    return (f"{name}.py" in text or re.search(
        rf"\b(?:import\s+{preceding}{re.escape(name)}\b|"
        rf"from\s+{re.escape(name)}\s+import\b)", text) is not None)


def select_modules(changed, root_sources, case_sources):
    """Pure selector: source maps use bare module names as keys."""
    reasons = {}
    shared = sorted(set(changed) & FULL_SUITE_FILES)
    if shared:
        return Selection({name: shared for name in case_sources},
                         "测试公共文件改动：" + "、".join(shared))
    root_names = set(root_sources) | {
        Path(path).stem for path in changed if "/" not in path and path.endswith(".py")
    }
    imports = {}
    try:
        for name, source in root_sources.items():
            dependencies = set()
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Import):
                    dependencies.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                    dependencies.add(node.module.split(".")[0])
            imports[name] = dependencies & root_names
    except SyntaxError as exc:
        return Selection({}, f"根模块 AST 解析失败：{name}（{exc}）")
    for path in sorted(set(changed)):
        hits = set()
        if path.startswith("tests/cases/") and path.endswith(".py"):
            name = Path(path).stem
            if name in case_sources:
                hits.add(name)
        elif "/" not in path and path.endswith(".py"):
            affected = {Path(path).stem}
            while True:
                expanded = affected | {name for name, deps in imports.items() if deps & affected}
                if expanded == affected:
                    break
                affected = expanded
            hits = {name for name, text in case_sources.items()
                    if any(references_module(text, module) for module in affected)}
        else:
            hits = {name for name, text in case_sources.items()
                    if path in text or Path(path).name in text}
        for name in sorted(hits):
            reasons.setdefault(name, []).append(path)
        if not hits and not is_document(path):
            return Selection({name: reasons.get(name, []) + [path] for name in case_sources},
                             f"改动文件没有匹配用例模块：{path}")
    return Selection(reasons)


def select_from_repo(root, changed, modules):
    try:
        root_sources = {path.stem: path.read_text(encoding="utf-8")
                        for path in Path(root).glob("*.py")}
        case_sources = {name: (Path(root) / "tests/cases" / f"{name}.py").read_text(
            encoding="utf-8") for name in modules}
    except (OSError, UnicodeError) as exc:
        return Selection({}, f"无法读取选择所需源码：{exc}")
    if "select_cases" in case_sources:
        case_sources["select_cases"] = ""  # 夹具里写满文件名，只按自身路径选中
    return select_modules(changed, root_sources, case_sources)
