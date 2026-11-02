"""Account-scoped warmup snapshot handoff. Trusted local pickle files only."""
from pathlib import Path
import copy
import runpy
import shutil
import tempfile
import ast
import pandas as pd
import numpy as np


def account_minute(account):
    """分钟偏移只从账户hour_offset读取，不从runtime维护第二份配置。"""
    offset = account['hour_offset']
    if not isinstance(offset, str) or not offset.endswith('m') or not offset[:-1].isdigit():
        raise ValueError('hour_offset must be a minute string such as 40m')
    minute = int(offset[:-1])
    if not 0 <= minute < 60:raise ValueError('hour_offset must be between 0m and 59m')
    return minute


def effective_settings(values):
    """配置比较投影；仅浅拷贝小字典，不复制策略池或读取大状态文件。"""
    from positions.ModelRobust40Strategy import effective_parent_params
    if not isinstance(values, dict):return values
    strategy = values.get('strategy_config', {})
    if not isinstance(strategy, dict) or strategy.get('name') != 'ModelRobust40Strategy' or 'params' not in strategy:
        return values
    params = dict(strategy['params'])
    if 'parent' in params:params['parent'] = effective_parent_params(params['parent'])
    # 旧snapshot可能有minute；真实偏移仍由account.hour_offset及状态时间轴核对。
    if 'runtime' in params:
        params['runtime'] = {k:v for k,v in params['runtime'].items() if k != 'minute'}
    result = dict(values, strategy_config=dict(strategy, params=params))
    # server worker在初始化/续算均覆盖为None；手工预热和普通策略仍需比较历史覆盖要求。
    if params.get('runtime', {}).get('server_init'):
        result.pop('required_history_start', None)
    return result


def settings(path):
    from positions.ModelRobust40Strategy import normalized
    d = runpy.run_path(str(Path(path).resolve()))
    # 参数由账户提供；比较时只排除明确失效的字段，不要求旧研究文件存在。
    strategy = copy.deepcopy(d['strategy_config'])
    runtime = strategy.get('params', {}).get('runtime', {})
    options = dict(runtime)
    options['minute'] = account_minute(d['account_config'])
    if runtime:
        strategy['params']['runtime'] = {k:runtime[k] for k in ('server_init',) if k in runtime}
    values = {k: d.get(k) for k in ('strategy_name','strategy_pool','simulator_config',
              'black_list','white_list','leverage','required_history_start','precompute_factor_configs')}
    values['strategy_config'] = strategy
    # Existing settings comparison rebuilds old mixed-source packages; no migration.
    values['simulation_data_policy'] = 'public_universe_exginfo_units_v1'
    values['account'] = {k: d.get('account_config', {}).get(k) for k in
                         ('hour_offset', 'exchange_name', 'account_type', 'filter_bstocks',
                          'contract_types', 'rebalance_mode', 'order_spot_money_limit',
                          'order_swap_money_limit')}
    for key in ('get_kline_num', 'get_kline_num_spot', 'min_kline_num'):
        values[key] = d.get(key)
    return normalized(effective_settings(values)), options


def cutoff(value):
    return pd.to_datetime(value, utc=True).tz_localize(None)


def validate_snapshot(snapshot, bundle):
    from .offline import canonical
    snapshot = Path(snapshot)
    end = cutoff(bundle['context']['index'][-1])
    if cutoff((snapshot/'snapshot_time.txt').read_text().strip()) != end:
        raise ValueError('Snapshot marker and MODEL cutoff differ')
    if not bundle['result']['parent'].index.equals(bundle['context']['index']):
        raise ValueError('MODEL time axes differ')
    if end.minute != bundle['minute'] or end + pd.Timedelta(hours=1) > pd.Timestamp.now(tz='UTC').tz_localize(None):
        raise ValueError('Snapshot offset or closed bar invalid')
    for i, spec in enumerate(bundle['strategy_pool']):
        child = snapshot/f'S{i+1}-{spec["name"]}'
        selected = pd.read_pickle(child/'选币结果.pkl')
        if len(selected) and cutoff(selected.candle_begin_time.max()) > end:
            raise ValueError('Selection includes future bars')
        ratios = []
        for kind in ('spot','swap'):
            frame = pd.read_pickle(child/f'ratio_{kind}.pkl')
            if not len(frame.index) or cutoff(frame.index[-1]) != end:
                raise ValueError('Ratio cutoff differs')
            last = frame.iloc[-1].copy()
            last.index = [kind+':'+canonical(c) for c in last.index]
            ratios.append(last)
        actual = pd.concat(ratios)
        expected = pd.Series(bundle['context']['matrices'][i][-1].toarray().ravel(),
                             index=bundle['context']['columns'])
        actual, expected = actual.align(expected, fill_value=0)
        if not np.array_equal(actual.to_numpy(), expected.to_numpy()):
            raise ValueError('Snapshot and MODEL last positions differ')
        for state in (('state0','state1') if spec.get('re_timing') else ('state0',)):
            lots = pd.read_pickle(child/f'lots_{state}.pkl')
            equity = pd.read_pickle(child/f'account_{state}.pkl')
            if cutoff(lots['simulate_to']) != end or cutoff(equity.candle_begin_time.iloc[-1]) != end:
                raise ValueError('Child simulator cutoff differs')
            for kind in ('spot','swap'):
                child_units = pd.Series(np.asarray(lots[kind]['sim'].lot_sizes),
                    index=[canonical(c) for c in lots[kind]['columns']])
                columns = [c.split(':',1)[1] for c in bundle['context']['columns'] if c.startswith(kind+':')]
                model_units = pd.Series(bundle['context']['simulation'][kind+'_lot_sizes'],index=columns)
                if not np.allclose(child_units,model_units.reindex(child_units.index),rtol=1e-12,atol=0):
                    raise ValueError(f'Child and MODEL instrument units differ: S{i+1} {kind} {state}')
    return end


def opted_in(path):
    # Do not execute arbitrary legacy account configs merely to discover opt-in.
    tree = ast.parse(Path(path).read_text(encoding='utf-8-sig'))
    strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    strings.update(n.arg for n in ast.walk(tree) if isinstance(n, ast.keyword))
    return {'ModelRobust40Strategy', 'warmup_path'}.issubset(strings)


def export_package(account, snapshot, output, expected_settings=None):
    output = Path(output)
    bundle = pd.read_pickle(output/'runtime_bootstrap.pkl')
    validate_snapshot(snapshot, bundle)
    values, _ = settings(account)
    if expected_settings is not None and values != effective_settings(expected_settings):
        raise ValueError('Account settings changed during warmup; rerun')
    shutil.copytree(snapshot, output/'snapshot')
    validate_snapshot(output/'snapshot', bundle)
    # Written last: incomplete packages cannot be restored.
    pd.to_pickle(dict(account=Path(account).stem, settings=values), output/'warmup.pkl')


def prepare_startup(accounts, root, clean_start=True):
    """Validate/stage opted-in accounts BEFORE startup clears snapshots."""
    root = Path(root).resolve()
    plans = []
    states = set()
    for account in accounts:
        if not opted_in(account):continue
        values, options = settings(account)
        if options.get('server_init') is True:continue
        if not options.get('warmup_path'):continue
        if values['strategy_config']['name'] != 'ModelRobust40Strategy':
            raise ValueError('warmup_path is only supported by ModelRobust40Strategy')
        package = Path(options['warmup_path'])
        if not package.is_absolute():package = root/package
        meta = pd.read_pickle(package/'warmup.pkl')
        if meta['account'] != Path(account).stem or effective_settings(meta['settings']) != values:
            raise ValueError(f'{Path(account).stem}: warmup configuration changed; rebuild required')
        name = Path(account).stem+'_'+values['strategy_name']
        destination = root/'data/snapshot'/name
        state = Path(options['state_path'])
        if not state.is_absolute():state = root/state
        if state.resolve() in states:raise ValueError('Warmup accounts share a MODEL state path')
        states.add(state.resolve())
        for cleared in ('snapshot', 'cache', 'runtime', 'warmup_handoff'):
            if state.resolve().is_relative_to((root/'data'/cleared).resolve()):
                raise ValueError('MODEL state must be outside cleaned directories')
        # On restart retain the current complete pair, never rewind an advanced state.
        model = state if state.exists() else package/'runtime_bootstrap.pkl'
        snapshot = destination if state.exists() else package/'snapshot'
        if not clean_start and destination.exists() and not state.exists():
            raise ValueError('Existing snapshots have no paired MODEL state; use clean startup')
        bundle = pd.read_pickle(model)
        if bundle.get('account_identity') != Path(account).stem or bundle['minute'] != options['minute']:
            raise ValueError('Warmup account or minute mismatch')
        validate_snapshot(snapshot, bundle)
        staging_parent = root/'data/warmup_handoff'
        staging_parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=Path(account).stem+'-', dir=staging_parent))
        shutil.copytree(snapshot, staging/'snapshot')
        shutil.copy2(model, staging/'model.pkl')
        validate_snapshot(staging/'snapshot', pd.read_pickle(staging/'model.pkl'))
        plans.append((staging, destination, state))
    return plans


def restore_startup(plans):
    """Restore only after cleanup; preserve staging if anything fails."""
    for staging, destination, state in plans:
        if destination.exists():
            validate_snapshot(destination, pd.read_pickle(staging/'model.pkl'))
        else:
            shutil.copytree(staging/'snapshot', destination)
        if not state.exists():
            state.parent.mkdir(parents=True, exist_ok=True)
            temporary = state.with_name(state.name+'.warmup.tmp')
            shutil.copy2(staging/'model.pkl', temporary)
            temporary.replace(state)
        validate_snapshot(destination, pd.read_pickle(state))
        shutil.rmtree(staging)
