#!/usr/bin/env bash
# 安装 / 更新 aiw 到本机。幂等：重复跑不会重复追加，改动前先备份。
#   git clone <仓库> ~/.claude/aiw && ~/.claude/aiw/install.sh
#   --claude-hook=settings|plugin：默认 settings；plugin 删除 settings 中的派单钩子，交给插件管理。
# 做五件事：
#   1. ~/.claude/CLAUDE.md 引入 @~/.claude/aiw/CLAUDE.md（已有内容保留）
#   2. ~/.claude/settings.json 加 UserPromptSubmit 派单钩子（其它设置保留）
#   3. ~/.local/bin/aiw 软链到本仓库，必要时把 ~/.local/bin 加进 PATH
#   4. 本机有 Codex（~/.codex 或 codex 命令）时：~/.codex/AGENTS.md 写入/刷新标记块，~/.codex/hooks.json 加派单钩子
#      （跳过：AIW_NO_CODEX=1）
#   5. 校验派单规则，跑一次 aiw doctor
# 先预检所有要改的 JSON，坏了一处都不动；JSON/标记块用临时文件原子替换。
# 兼容 macOS 自带 bash 3.2。
set -euo pipefail

CLAUDE_HOOK=settings
for arg in "$@"; do
  case "$arg" in
    --claude-hook=settings|--claude-hook=plugin) CLAUDE_HOOK="${arg#*=}" ;;
    *) echo "用法：$0 [--claude-hook=settings|plugin]" >&2; exit 64 ;;
  esac
done

src="${BASH_SOURCE[0]}"
while [ -L "$src" ]; do
  dir="$(cd -P "$(dirname "$src")" && pwd)"
  src="$(readlink "$src")"
  case "$src" in /*) ;; *) src="${dir}/${src}" ;; esac
done
H="$(cd -P "$(dirname "$src")" && pwd)"
CLAUDE_DIR="${HOME}/.claude"
STAMP="$(date +%Y%m%d-%H%M%S)"
source "${H}/env_compat.sh"
PY="${AIW_PYTHON-python3}"

command -v "$PY" >/dev/null || { echo "需要 python3" >&2; exit 1; }

# 0. 预检：要改的 JSON 先全部解析一遍，任何一个坏了就一处都不动
CHECK_FILES=("${CLAUDE_DIR}/settings.json")
if [ -z "${AIW_NO_CODEX:-}" ] && { [ -d "${HOME}/.codex" ] || command -v codex >/dev/null 2>&1; }; then
  CHECK_FILES+=("${HOME}/.codex/hooks.json")
fi
"$PY" - "${CHECK_FILES[@]}" <<'PYEOF'
import json, sys
from pathlib import Path
bad = []
for f in map(Path, sys.argv[1:]):
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("顶层必须是对象")
            hooks = data.get("hooks", {})
            if not isinstance(hooks, dict):
                raise ValueError("hooks 必须是对象")
            groups = hooks.get("UserPromptSubmit", [])
            if not isinstance(groups, list):
                raise ValueError("hooks.UserPromptSubmit 必须是列表")
            for group in groups:
                if not isinstance(group, dict):
                    raise ValueError("UserPromptSubmit 每个组必须是对象")
                if not isinstance(group.get("hooks", []), list):
                    raise ValueError("组内 hooks 必须是列表")
        except (OSError, ValueError) as e:
            bad.append(f"{f}（{e}）")
if bad:
    sys.exit("✗ 以下文件 JSON 语法或结构不合法，没有做任何改动。修好后重跑：\n  " + "\n  ".join(bad))
PYEOF

mkdir -p "${CLAUDE_DIR}" "${H}/state"
if [ ! -e "${CLAUDE_DIR}/aiw" ] && [ ! -L "${CLAUDE_DIR}/aiw" ]; then
  ln -s "$H" "${CLAUDE_DIR}/aiw"
fi

# 1. CLAUDE.md
"$PY" - "$CLAUDE_DIR/CLAUDE.md" "$HOME/.config/aiw/CLAUDE.personal.md" "$STAMP" <<'PYEOF'
import shutil, sys
from pathlib import Path
md, personal, stamp = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
IMPORT_LINE = '@~/.claude/aiw/CLAUDE.md'
PERSONAL_LINE = '@~/.config/aiw/CLAUDE.personal.md'
old = md.read_text(encoding="utf-8") if md.exists() else ""
new = old.replace('@~/.claude/ai-harness/CLAUDE.md', IMPORT_LINE)
# Collapse duplicate project imports, preserving the first position.
seen = False
lines = []
for line in new.splitlines(keepends=True):
    if line.rstrip('\r\n') == IMPORT_LINE:
        if seen:
            continue
        seen = True
    lines.append(line)
new = ''.join(lines)
if IMPORT_LINE not in new.splitlines():
    new += ("\n" if new else "") + IMPORT_LINE + "\n"
if personal.exists():
    lines = [line for line in new.splitlines(keepends=True) if line.rstrip("\r\n") != PERSONAL_LINE]
    index = next(i for i, line in enumerate(lines) if line.rstrip("\r\n") == IMPORT_LINE)
    if not lines[index].endswith("\n"):
        lines[index] += "\n"
    lines.insert(index + 1, PERSONAL_LINE + "\n")
    new = "".join(lines)
elif personal.is_symlink():
    print(f"⚠ {personal} 是断链，视为无个人规则")
if new != old:
    if md.exists():
        shutil.copy2(md, md.with_name(md.name + ".bak-" + stamp))
    tmp = md.with_name(md.name + ".tmp-aiw")
    tmp.write_text(new, encoding="utf-8")
    tmp.replace(md)
    print(f"✓ {md} 更新规则引入")
else:
    print(f"✓ {md} 已引入规则")
PYEOF

# 2. settings.json 钩子（用 python 合并 JSON，不覆盖其它设置）
"$PY" - "${CLAUDE_DIR}/settings.json" "$STAMP" "$CLAUDE_HOOK" <<'PYEOF'
import json, shutil, sys
from pathlib import Path
def atomic_write(p, text):
    tmp = p.with_name(p.name + ".tmp-aiw")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)
p, stamp = Path(sys.argv[1]), sys.argv[2]
cmd = 'python3 "$HOME/.claude/aiw/route.py" --hook'
data = {}
if p.exists():
    raw = p.read_text(encoding="utf-8")
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        sys.exit(f"✗ {p} 不是合法 JSON（{e}），没有改动。修好后重跑。")
if sys.argv[3] == "plugin":
    hooks = data.get("hooks", {})
    groups = hooks.get("UserPromptSubmit", [])
    kept_groups = []
    changed = False
    for group in groups:
        items = group.get("hooks", [])
        kept = [h for h in items if not (
            isinstance(h, dict) and isinstance(h.get("command"), str)
            and any(x in h["command"] for x in ("aiw/route.py", "ai-harness/route.py")))]
        if len(kept) == len(items):
            kept_groups.append(group)
        else:
            changed = True
            if kept:
                kept_groups.append(dict(group, hooks=kept))
    if changed:
        if kept_groups:
            hooks["UserPromptSubmit"] = kept_groups
        else:
            hooks.pop("UserPromptSubmit", None)
        shutil.copy2(p, p.with_name(p.name + ".bak-" + stamp))
        atomic_write(p, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        print(f"✓ 派单钩子由插件管理，已删除 {p} 中的派单钩子（备份 {p.name}.bak-{stamp}）")
    else:
        print("✓ 派单钩子由插件管理，settings.json 无需改动")
    sys.exit(0)
hooks = data.setdefault("hooks", {})
groups = hooks.setdefault("UserPromptSubmit", [])
mine = [h for g in groups for h in g.get("hooks", []) if any(x in h.get("command", "") for x in ("aiw/route.py", "ai-harness/route.py"))]
stale = [h for h in mine if h.get("command") != cmd or h.get("timeout") != 10]
if mine and not stale:
    print(f"✓ {p} 已有派单钩子")
elif mine:
    shutil.copy2(p, p.with_name(p.name + ".bak-" + stamp))
    for h in stale:
        h.update(type="command", command=cmd, timeout=10)
    atomic_write(p, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(f"✓ {p} 刷新旧的派单钩子（备份 {p.name}.bak-{stamp}）")
else:
    if p.exists():
        shutil.copy2(p, p.with_name(p.name + ".bak-" + stamp))
    groups.append({"hooks": [{"type": "command", "command": cmd, "timeout": 10}]})
    atomic_write(p, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(f"✓ {p} 加入派单钩子" + (f"（备份 {p.name}.bak-{stamp}）" if p.with_name(p.name + '.bak-' + stamp).exists() else ""))
PYEOF

# 3. aiw 命令
chmod +x "${H}/bin/aiw" "${H}/install.sh"
mkdir -p "${HOME}/.local/bin"
ln -sfn "${H}/bin/aiw" "${HOME}/.local/bin/aiw"
echo "✓ ~/.local/bin/aiw → ${H}/bin/aiw"
"$PY" - "$HOME/.zshrc" "$STAMP" "$PATH" <<'PYEOF'
import os, shutil, sys
from pathlib import Path
p, stamp = Path(sys.argv[1]), sys.argv[2]
old = p.read_text() if p.exists() else ''
mark = '# aiw: on PATH'
new = old.replace('# ai-harness: aiw on PATH', mark)
if mark not in new.splitlines() and str(Path.home() / '.local/bin') not in sys.argv[3].split(':'):
    new += '\n' + mark + '\nexport PATH="$HOME/.local/bin:$PATH"\n'
if new != old:
    if p.exists():
        shutil.copy2(p, p.with_name(p.name + '.bak-' + stamp))
    p.write_text(new)
PYEOF

# 4. Codex（主会话也可以是 Codex）
CODEX_DIR="${HOME}/.codex"
if [ -z "${AIW_NO_CODEX:-}" ] && { [ -d "$CODEX_DIR" ] || command -v codex >/dev/null 2>&1; }; then
  mkdir -p "$CODEX_DIR"
  "$PY" - "$CODEX_DIR" "${H}/AGENTS.harness.md" "$STAMP" <<'PYEOF'
import json, shutil, sys
from pathlib import Path
def atomic_write(p, text):
    tmp = p.with_name(p.name + ".tmp-aiw")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)
d, src, stamp = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
BEGIN, END = "<!-- aiw:begin（install.sh 生成，勿手改） -->", "<!-- aiw:end -->"
content = src.read_text(encoding='utf-8').strip()
personal = d.parent / '.config/aiw/AGENTS.personal.md'
if personal.exists():
    content += "\n\n" + personal.read_text(encoding='utf-8').strip()
elif personal.is_symlink():
    print(f"⚠ {personal} 是断链，视为无个人规则")
block = f"{BEGIN}\n{content}\n{END}\n"
md = d / "AGENTS.md"
old = md.read_text(encoding="utf-8") if md.exists() else ""
# Normalize legacy delimiters before refreshing the whole block.
normalized = old.replace('<!-- ai-harness:begin', '<!-- aiw:begin').replace('<!-- ai-harness:end -->', END)
if BEGIN in normalized and END in normalized:
    head, rest = normalized.split(BEGIN, 1)
    new = head + block + rest.split(END, 1)[1].lstrip("\n")
else:
    new = old + ("\n" if old and not old.endswith("\n") else "") + ("\n" if old else "") + block
if new == old:
    print(f"✓ {md} 已是最新")
else:
    if md.exists():
        shutil.copy2(md, md.with_name(md.name + ".bak-" + stamp))
    atomic_write(md, new)
    print(f"✓ {md} 写入 aiw 标记块" + ("（备份 AGENTS.md.bak-" + stamp + "）" if old else ""))
hp = d / "hooks.json"
data = {}
if hp.exists():
    raw = hp.read_text(encoding="utf-8")
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        sys.exit(f"✗ {hp} 不是合法 JSON（{e}），没有改动。修好后重跑。")
groups = data.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])
cmd = 'python3 "$HOME/.claude/aiw/route.py" --hook'
mine = [h for g in groups for h in g.get("hooks", []) if any(x in h.get("command", "") for x in ("aiw/route.py", "ai-harness/route.py"))]
stale = [h for h in mine if h.get("command") != cmd or h.get("timeout") != 10]
if mine and not stale:
    print(f"✓ {hp} 已有派单钩子")
elif mine:
    shutil.copy2(hp, hp.with_name(hp.name + ".bak-" + stamp))
    for h in stale:
        h.update(type="command", command=cmd, timeout=10)
    atomic_write(hp, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(f"✓ {hp} 刷新旧的派单钩子（备份 {hp.name}.bak-{stamp}）")
else:
    if hp.exists():
        shutil.copy2(hp, hp.with_name(hp.name + ".bak-" + stamp))
    groups.append({"hooks": [{"type": "command", "command": cmd, "timeout": 10}]})
    atomic_write(hp, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(f"✓ {hp} 加入派单钩子（Codex 下次启动会请你确认信任这个新钩子）")
PYEOF
else
  echo "· 未发现 Codex，跳过 Codex 接入"
fi

# 5. 校验派单规则（坏了只提示，不中断）
"$PY" "${H}/route.py" --check || echo "↑ 派单规则有问题，钩子会跳过派单，修好后再跑 aiw route --check。"

# 6. 体检（失败不中断安装）
echo
"${H}/bin/aiw" doctor || echo "↑ 有座位没装好或未在 PATH，装好后再跑 aiw doctor。"
echo
echo "完成。下一步：按需完成各 CLI 的官方登录（如 codex login），然后 aiw doctor"
