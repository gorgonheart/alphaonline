"""Deployment helpers: machine credentials and stopped-installation checks only."""
import argparse
import json
import os
from pathlib import Path


CREDENTIAL_FIELDS = {'apiKey', 'secret', 'password', 'passphrase', 'wechat_webhook_url'}


def account_credentials(path, name, settings):
    """Overlay private connection fields; all strategy/execution parameters stay in accounts."""
    path = Path(path)
    if not path.exists():
        return settings
    values = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(values, dict):
        raise ValueError('accounts.local.json must contain account credentials objects')
    local = values.get(name, {})
    if not isinstance(local, dict) or set(local) - CREDENTIAL_FIELDS:
        raise ValueError(f'{name}: only connection credentials are allowed in accounts.local.json')
    return {**settings, **local}


def assert_stopped(root):
    """Linux only; reject Python workers using this installation, excluding this probe.

    Processes whose cwd has been deleted belong to retired installations, not a
    newly created directory at the same pathname. Never terminate them here.
    """
    root = Path(root).resolve()
    active = []
    for proc in Path('/proc').glob('[0-9]*'):
        if proc.name == str(os.getpid()):
            continue
        try:
            cwd = os.readlink(proc/'cwd')
            if cwd.endswith(' (deleted)'):
                continue
            args = (proc/'cmdline').read_bytes().decode(errors='replace').split('\0')
            if not args or 'python' not in Path(args[0]).name.lower():
                continue
            in_root = Path(cwd) == root or root in Path(cwd).parents
            named_root = any(a.startswith(str(root)+'/') for a in args[1:])
            if in_root or named_root:
                active.append(proc.name)
        except (OSError, PermissionError):
            continue
    if active:
        raise RuntimeError('Stop this installation and its workers before updating; PIDs: '+', '.join(active))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['check', 'stopped'])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.mode == 'stopped':
        assert_stopped(root)
        print('No active Python workers use this installation.')
        return
    config = json.loads((root/'config.json').read_text(encoding='utf-8'))
    producer = Path(config['realtime_data_path'])
    if not producer.is_dir():
        raise ValueError('Configured realtime_data_path does not exist')
    if not isinstance(config.get('proxy', {}), dict):
        raise ValueError('proxy must be a JSON object')
    for account in (root/'accounts').glob('*.py'):
        if not account.name.startswith('_'):
            account_credentials(root/'accounts.local.json', account.stem, {})
    print(f'Machine configuration valid; producer data: {producer}')
    print('No account initialization, exchange requests or service start performed.')


if __name__ == '__main__':
    main()
