"""Read aiw environment settings with Python and state path defaults."""
import os
from pathlib import Path


def session_id():
    """Main-session identity, preferring Claude when both are present."""
    return os.environ.get('CLAUDE_CODE_SESSION_ID') or os.environ.get('CODEX_THREAD_ID') or None


def valid_tui_id(value):
    import re
    return bool(value and re.fullmatch(r'[A-Za-z0-9_-]+', value))


def write_tui_session(state, thread=None, transcript=None):
    """Publish Codex's thread without contacting the tmux server."""
    import sys
    import tempfile
    tui_id = os.environ.get('AIW_TUI_ID')
    if (os.environ.get('AIW_NESTED') == '1' or not valid_tui_id(tui_id)
            or (thread is None and os.environ.get('CLAUDE_CODE_SESSION_ID'))):
        return
    thread = thread if thread is not None else session_id()
    if not isinstance(thread, str) or not thread or '\n' in thread or '\r' in thread:
        return
    temporary = None
    try:
        folder = state / 'tui'
        folder.mkdir(parents=True, exist_ok=True)
        if transcript is None:
            try:
                lines = (folder / tui_id).read_text().splitlines()
                transcript = lines[1] if len(lines) > 1 else None
            except (OSError, UnicodeError):
                pass
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder,
                                         prefix='.', delete=False) as stream:
            temporary = stream.name
            stream.write(thread + '\n')
            if isinstance(transcript, str) and transcript and '\n' not in transcript and '\r' not in transcript:
                stream.write(transcript + '\n')
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


def agent_use_home() -> Path:
    """Use the new default, retaining an existing legacy checkout in place."""
    explicit = os.environ.get('AGENT_USE_HOME')
    if explicit:
        return Path(explicit).expanduser()
    default = Path.home() / '.local/share/agent-use'
    legacy = Path.home() / 'code/ai/agent-use'
    if not (default.exists() or default.is_symlink()) and (legacy.is_dir() or legacy.is_symlink()):
        return legacy
    return default
