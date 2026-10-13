# Shared optional ecosystem paths and Git operations (bash 3.2).
aiw_git() {
  # Keep this list identical to gitenv.py repository_git_env().
  env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_COMMON_DIR \
    -u GIT_OBJECT_DIRECTORY -u GIT_ALTERNATE_OBJECT_DIRECTORIES \
    -u GIT_CEILING_DIRECTORIES -u GIT_DISCOVERY_ACROSS_FILESYSTEM \
    -u GIT_NAMESPACE -u GIT_PREFIX -u GIT_SHALLOW_FILE -u GIT_GRAFT_FILE \
    git "$@"
}

aiw_agent_use_paths() {
  AU="$(PYTHONPATH="$H${PYTHONPATH:+:$PYTHONPATH}" "$PY" -c 'from env_compat import agent_use_home; print(agent_use_home())')"
  ADAPTERS="$("$PY" - "$AU" <<'PYEOF'
import os, sys
from pathlib import Path
print(Path(os.environ.get('AIW_ADAPTERS_HOME') or Path(sys.argv[1]).parent / 'agent-use-adapters').expanduser())
PYEOF
)"
}

aiw_is_git_repo() {
  # A plain directory inside another checkout is not itself a repository.
  [ -d "$1" ] && [ "$(aiw_git -C "$1" rev-parse --show-toplevel 2>/dev/null)" = "$(cd -P "$1" && pwd)" ]
}

aiw_pull_optional() {
  if aiw_git -C "$1" pull --ff-only; then
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
  elif mkdir -p "$(dirname "$1")" && aiw_git clone "$3" "$1"; then
    echo "✓ $2 已克隆"
  else
    echo "⚠ $2 克隆失败，跳过该仓库后续步骤"
    return 1
  fi
}
