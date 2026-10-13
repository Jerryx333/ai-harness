#!/usr/bin/env python3
# 用途：串行检查免费座位并管理每日 LaunchAgent。
# 运行：aiw freecheck [--install|--uninstall]；依赖：Python 标准库、agent-use、discord-use。
from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import urllib.request
from pathlib import Path

import ai_worker as w

LABEL = 'com.ai-harness.freecheck'


def install(uninstall=False):
    directory = Path(os.environ.get('AIW_LAUNCH_AGENTS_DIR', str(Path.home() / 'Library/LaunchAgents'))).expanduser()
    path = directory / (LABEL + '.plist')
    domain = f'gui/{os.getuid()}'
    if not uninstall:
        directory.mkdir(parents=True, exist_ok=True)
        w.STATE.mkdir(parents=True, exist_ok=True)
        paths = [str(Path.home() / '.local/bin'), str(Path.home() / '.opencode/bin'),
                 '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin']
        config = {'Label': LABEL, 'ProgramArguments': [str(w.HERE / 'bin/aiw'), 'freecheck'],
                  'StartCalendarInterval': {'Hour': 9, 'Minute': 30},
                  'EnvironmentVariables': {'PATH': ':'.join(paths)},
                  'StandardOutPath': str(w.STATE / 'freecheck.log'),
                  'StandardErrorPath': str(w.STATE / 'freecheck.log')}
        for key in ("AI_HARNESS_STATE", "AGENT_USE_HOME", "AGENT_USE_CONFIG", "XDG_CONFIG_HOME", "AI_HARNESS_PYTHON",
                    "AIW_CONFIG", "AIW_ROUTER_RULES", "AGENT_USE_ERRAND_HOME"):
            if os.environ.get(key):
                config["EnvironmentVariables"][key] = os.environ[key]
        path.write_bytes(plistlib.dumps(config))
    subprocess.run(['launchctl', 'bootout', domain, str(path)], capture_output=True, check=False)
    if uninstall:
        path.unlink(missing_ok=True)
        print(f'已卸载：{path}')
    else:
        subprocess.run(['launchctl', 'bootstrap', domain, str(path)], check=True, capture_output=True)
        print(f'已安装每日 09:30 检查：{path}')
    return 0


def zen_check():
    seats = [name for name, cfg in w.SEATS['seats'].items()
             if cfg.get('kind') == 'agent_use' and cfg.get('provider') == 'opencode']
    if not seats:
        return '没有配置对应适配器座位，跳过目录检查'
    try:
        config = w.load_json(w.agent_use_registry(), {})
    except w.StateError as exc:
        return f'agent-use 注册表不可用：{exc}；不据此停用'
    providers = config.get('providers', {})
    cfg = providers.get('opencode', {})
    candidates = [cfg.get('default_model', '')]
    models = cfg.get('models', [])
    candidates.extend(models if isinstance(models, (list, dict)) else models.split(','))
    fallbacks = cfg.get('options', {}).get('fallback_models', [])
    candidates.extend(fallbacks.split(',') if isinstance(fallbacks, str) else fallbacks)
    configured = {m.strip() for m in candidates if isinstance(m, str) and m.strip().endswith('-free')}
    if not configured:
        return '没有配置 -free 模型，跳过目录检查'
    url = os.environ.get('AIW_ZEN_MODELS_URL', 'https://opencode.ai/zen/v1/models')
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ai-harness-freecheck/1"}), timeout=20) as response:
        data = json.load(response)
    # 无效或异常短的目录不能当成模型下架。
    rows = data['data']
    if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get('id'), str) for r in rows):
        raise ValueError('invalid model list')
    if len(rows) < 10:
        print('[freecheck] Zen 目录异常：少于 10 项，不判定下架', file=sys.stderr)
        return 'Zen 目录异常，跳过下架判定'
    available = {r['id'] for r in rows}
    missing = configured - available
    if missing == configured:
        for seat in seats:
            w.disable_seat(seat, 'Zen 免费模型全部下架：' + ', '.join(sorted(missing)))
        return 'Zen 免费模型全部下架，已停用'
    if missing:
        w.notify_missing_models(missing)
        return '部分免费模型下架，已提醒'
    return 'Zen 免费模型仍在列表'


def check():
    errands = w.errand_names()
    w.retry_notifications()
    results = []
    newly_disabled = False
    seen = set()
    for seat, cfg in w.SEATS['seats'].items():
        if seat in errands:
            results.append((seat, '跑腿供应商，跳过；请用 aiw errand'))
            continue
        if cfg.get('disabled_by_user'):
            results.append((seat, '已被用户停用，跳过：' + cfg['disabled_by_user']))
            continue
        if not cfg.get('free_guard'):
            continue
        if w.disabled_seat(seat):
            results.append((seat, '已停用，跳过'))
            continue
        detail = ''
        if cfg.get('provider') == 'opencode':
            try:
                detail = zen_check() + '；'
            except (OSError, ValueError, KeyError, TypeError, w.StateError) as exc:
                detail = f'Zen 列表检查失败（{type(exc).__name__}），不据此停用；'
                print(f'[freecheck] {detail}', file=sys.stderr)
        if w.disabled_seat(seat):
            newly_disabled = True
            results.append((seat, detail + '已停用，跳过探测'))
            continue
        # 同一供应商模型只探测一次；固定 WorkBuddy 模型，禁用内部轮换。
        for model in w.available_models(cfg):
            if w.disabled_seat(seat):
                break
            if model not in w.available_models(cfg):
                continue
            key = f"{cfg.get('provider', seat)}:{model}"
            if key in seen:
                continue
            seen.add(key)
            result = w.run_seat(seat, 'Reply with exactly: FREECHECK_OK', str(w.HERE), 120, True,
                                fixed_model=model)
            if result.get('reason') == 'outside_free_window':
                results.append((seat, '不在免费时段，跳过'))
                continue
            w.ledger({'seat': seat, 'actual': seat, 'kind': result['kind'], 'rc': result['rc'],
                      'secs': result['secs'], 'ro': True, 'freecheck': True, 'model': model,
                      'run_dir': result.get('run_dir')})
            if result.get('reason') == 'promotion_expired':
                results.append((seat, '【需关注】免费活动已到期，需复核（free_until）；需更新 agent-use 生效的注册表（agent-use list 可查看路径）或停用该座位'))
                continue
            if result.get('disabled_key'):
                newly_disabled |= result.get('newly_disabled', False)
                outcome = '收费信号，已停用 ' + result['disabled_key']
            else:
                outcome = '探测成功' if result['kind'] == 'ok' else f"探测失败（{result['kind']}），未停用"
            results.append((seat, detail + outcome + (f'（{model}）' if model else '')))
    for seat, detail in results:
        print(f'{seat}: {detail}')
    return 2 if newly_disabled else 0


def main():
    if sys.argv[1:] == ['--install']:
        return install()
    if sys.argv[1:] == ['--uninstall']:
        return install(True)
    if sys.argv[1:]:
        print('用法：aiw freecheck [--install|--uninstall]', file=sys.stderr)
        return 64
    return check()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, subprocess.SubprocessError, w.StateError) as exc:
        print(f'[freecheck] 失败：{type(exc).__name__}', file=sys.stderr)
        sys.exit(1)
