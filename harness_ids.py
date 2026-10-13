"""harness 生成的 ID 格式与字面识别规则；不读取任何环境。"""
from __future__ import annotations

import re

RUN_TEMPLATE = '{stamp:%Y%m%d-%H%M%S}-{seat}-{pid}-{nonce}'
RECORD_TEMPLATE = '{host}-{stamp:%Y%m%d%H%M%S}-{pid}-{sequence}'
PANEL_TEMPLATE = '{stamp:%Y%m%d-%H%M%S}-{pid}'
DATE_TIME = r'[0-9]{8}-[0-9]{6}'
PID = r'[0-9]{1,7}'
RECORD_SUFFIX = r'-[0-9]{14}-' + PID + r'-[0-9]{1,4}'
RUN_SUFFIX = '-' + PID + r'-[0-9a-f]{8}'


def format_run_id(stamp, seat, pid, nonce):
    return RUN_TEMPLATE.format(stamp=stamp, seat=seat, pid=pid, nonce=nonce)


def format_record_id(host, stamp, pid, sequence):
    return RECORD_TEMPLATE.format(host=host, stamp=stamp, pid=pid, sequence=sequence)


def format_panel_id(stamp, pid):
    return PANEL_TEMPLATE.format(stamp=stamp, pid=pid)


def compile_id_pattern(hosts, seats):
    """只认由可信名称组成的完整 ID，保留历史数字宽度。"""
    patterns = [DATE_TIME + '-' + PID]
    for names, prefix, suffix in ((hosts, '', RECORD_SUFFIX),
                                   (seats, DATE_TIME + '-', RUN_SUFFIX)):
        literals = '|'.join(re.escape(name) for name in
                            sorted(filter(None, names), key=lambda name: (-len(name), name)))
        if literals:
            patterns.append(prefix + '(?:' + literals + ')' + suffix)
    return re.compile(r'(?<![A-Za-z0-9_-])(?:' + '|'.join(patterns) + r')(?![A-Za-z0-9_-])')
