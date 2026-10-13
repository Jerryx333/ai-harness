# Shared optional ecosystem paths and Git operations (bash 3.2).
aiw_agent_use_paths() {
  AU="$("$PY" -c 'import os; from pathlib import Path; print(Path(os.environ.get("AGENT_USE_HOME") or Path.home() / "code/ai/agent-use").expanduser())')"
  ADAPTERS="$("$PY" - "$AU" <<'PYEOF'
import os, sys
from pathlib import Path
print(Path(os.environ.get('AIW_ADAPTERS_HOME') or Path(sys.argv[1]).parent / 'agent-use-adapters').expanduser())
PYEOF
)"
}

aiw_is_git_repo() {
  # A plain directory inside another checkout is not itself a repository.
  [ -d "$1" ] && [ "$(git -C "$1" rev-parse --show-toplevel 2>/dev/null)" = "$(cd -P "$1" && pwd)" ]
}

aiw_pull_optional() {
  if git -C "$1" pull --ff-only; then
    echo "✓ $2 已更新（pull --ff-only）"
  else
    echo "⚠ $2 更新失败，继续（可自行检查仓库）"
  fi
}

aiw_install_repo() {
  if [ -e "$1" ] || [ -L "$1" ]; then
    if aiw_is_git_repo "$1"; then
      aiw_pull_optional "$1" "$2"
    else
      echo "⚠ $2 目录不是 git 仓库，跳过该仓库后续步骤"
      return 1
    fi
  elif mkdir -p "$(dirname "$1")" && git clone "$3" "$1"; then
    echo "✓ $2 已克隆"
  else
    echo "⚠ $2 克隆失败，跳过该仓库后续步骤"
    return 1
  fi
}
