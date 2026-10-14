#!/usr/bin/env python3
"""Codex main-session tmux wrapper and its status callback."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import uuid

from env_compat import getenv, valid_tui_id

HERE = Path(__file__).resolve().parent
STATUS_EXTRA_TIMEOUT = 3


def status(tui_id):
    line = _dispatch_status(tui_id)
    command = os.environ.get('AIW_TUI_STATUS_EXTRA', '')
    if command:
        try:
            argv = shlex.split(command)
            if not argv:
                return line
            result = subprocess.run(argv, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True,
                                    timeout=STATUS_EXTRA_TIMEOUT, check=True)
            extra = next((s.strip() for s in result.stdout.splitlines() if s.strip()), '')
            extra = extra.replace('#', '##')
            if len(extra) > 120:
                extra = extra[:119]
                # Keep escaped hashes paired at the truncation boundary.
                if (len(extra) - len(extra.rstrip('#'))) % 2:
                    extra = extra[:-1]
                extra += '…'
            if extra:
                return line + ' │ ' + extra
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return line


def _dispatch_status(tui_id):
    idle = '○ 派单空闲'
    try:
        if not valid_tui_id(tui_id):
            return idle
        state = Path(getenv('AIW_STATE', HERE / 'state'))
        try:
            thread = (state / 'tui' / tui_id).read_text().strip()
        except OSError:
            thread = ''
        board = shutil.which('aiw-board')
        if not board:
            return '派单 ?（未安装 aiw-board）' if thread else idle
        if not thread:
            thread = 'aiw-tui-' + tui_id
        result = subprocess.run([board, 'statusline'], input=json.dumps({'session_id': thread}),
                                capture_output=True, text=True, timeout=10)
        return result.stdout.splitlines()[0] if result.stdout else '派单 ?'
    except Exception:
        return '派单 ?'


def launch(args):
    if not shutil.which('tmux'):
        print('需要 tmux：brew install tmux', file=sys.stderr)
        return 127
    tui_id = uuid.uuid4().hex
    os.environ['AIW_TUI_ID'] = tui_id
    os.environ['AIW_STATE'] = str(Path(getenv('AIW_STATE', HERE / 'state')).absolute())
    # tmux's server may predate this shell's PATH, Python and state settings.
    environment = ['/usr/bin/env', 'PATH=' + os.environ.get('PATH', ''),
                   'AIW_PYTHON=' + sys.executable,
                   'AIW_STATE=' + os.environ['AIW_STATE']]
    if 'AIW_TUI_STATUS_EXTRA' in os.environ:
        environment.append('AIW_TUI_STATUS_EXTRA=' + os.environ['AIW_TUI_STATUS_EXTRA'])
    callback = shlex.join(environment + [str(HERE / 'bin' / 'aiw'), 'tui-status', tui_id])
    options = [('status', 'on'), ('status-interval', '15'), ('status-left', ''),
               ('status-right-length', '200'), ('status-right', '#(' + callback + ')')]
    if os.environ.get('TMUX'):
        for key, value in options:
            subprocess.run(['tmux', 'set-option', key, value], check=True)
        os.execvp('codex', ['codex', *args])
    else:
        name = 'aiw-' + tui_id
        command = 'exec ' + shlex.join(environment + ['AIW_TUI_ID=' + tui_id, 'codex', *args])
        argv = ['tmux', 'new-session', '-s', name, command]
        for key, value in [('status', 'on'), ('status-interval', '15'),
                           ('status-justify', 'left'), ('status-style', 'bg=default,fg=colour250'),
                           ('status-left', '#(' + callback + ')'), ('status-left-length', '200'),
                           ('status-right', ''), ('window-status-format', ''),
                           ('window-status-current-format', '')]:
            argv += [';', 'set-option', '-t', name, key, value]
        os.execvp('tmux', argv)


if __name__ == '__main__':
    if sys.argv[1] == 'status':
        print(status(sys.argv[2] if len(sys.argv) > 2 else ''))
    else:
        sys.exit(launch(sys.argv[2:]))
