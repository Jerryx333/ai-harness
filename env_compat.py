"""Environment rename compatibility; presence (including an empty value) wins."""
import os
import sys

LEGACY = {'AIW_STATE': 'AI_HARNESS_STATE', 'AIW_PYTHON': 'AI_HARNESS_PYTHON',
          'AIW_NO_CODEX': 'AI_HARNESS_NO_CODEX'}
_warned = set(os.environ.get("_AIW_ENV_WARNED", "").split(":"))


def getenv(name, default=None):
    old = LEGACY[name]
    if name in os.environ:
        if old in os.environ and os.environ[name] != os.environ[old] and name not in _warned:
            print(f'警告：{name} 与 {old} 值不同，使用 {name}；重跑 install.sh 并迁移旧变量', file=sys.stderr)
            _warned.add(name)
        value = os.environ[name]
    else:
        value = os.environ.get(old, default)
    if name == 'AIW_PYTHON':
        return value or 'python3'
    if name == 'AIW_STATE' and value is not None:
        return os.path.expanduser(value)
    return value
