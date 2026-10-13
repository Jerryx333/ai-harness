"""Read aiw environment settings with Python and state path defaults."""
import os


def session_id():
    """Main-session identity, preferring Claude when both are present."""
    return os.environ.get('CLAUDE_CODE_SESSION_ID') or os.environ.get('CODEX_THREAD_ID') or None


def valid_tui_id(value):
    import re
    return bool(value and re.fullmatch(r'[A-Za-z0-9_-]+', value))


def write_tui_session(state):
    """Publish Codex's thread without contacting the tmux server."""
    import sys
    import tempfile
    tui_id = os.environ.get('AIW_TUI_ID')
    if (os.environ.get('AIW_NESTED') == '1' or not valid_tui_id(tui_id)
            or os.environ.get('CLAUDE_CODE_SESSION_ID') or not session_id()):
        return
    temporary = None
    try:
        folder = state / 'tui'
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder,
                                         prefix='.', delete=False) as stream:
            temporary = stream.name
            stream.write(session_id() + '\n')
        os.replace(temporary, folder / tui_id)
    except OSError as exc:
        print(f'[ai-worker] 警告：写 tui 会话失败：{exc}', file=sys.stderr)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def getenv(name, default=None):
    value = os.environ.get(name, default)
    if name == 'AIW_PYTHON':
        return value or 'python3'
    if name == 'AIW_STATE' and value is not None:
        return os.path.expanduser(value)
    return value
