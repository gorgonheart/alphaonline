"""Statistics-only moving average estimates; no exchange requests or trading writes.

Called under the existing statistics account lock. Existing inventory is seeded
at the first observation price, not reconstructed from historical net cash flow.
"""
import json
import math
from pathlib import Path

import pandas as pd

from tools.statistics_runtime import atomic_save


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _orders(root):
    frames = []
    for path in sorted((root / '订单记录').glob('order_history_*.csv')):
        frame = pd.read_csv(path, encoding='gbk', dtype={'client_order_id': str})
        if frame.empty:
            continue
        required = {'symbol_type', 'symbol', 'side', 'executed_qty', 'quote_qty', 'client_order_id'}
        if not required.issubset(frame):
            raise ValueError('成本估算订单记录缺列')
        if 'account_name' in frame:
            frame = frame.loc[frame.account_name == root.name]
        frame = frame.loc[frame.symbol_type == 'spot'].copy()
        if 'order_status' in frame:
            frame = frame.loc[frame.order_status.isin(['FILLED', 'PARTIALLY_FILLED'])]
        frame['_time'] = pd.to_datetime(frame.get('exec_time', pd.Series(pd.NaT, index=frame.index)), errors='coerce', utc=True)
        if 'run_time' in frame:
            fallback = pd.to_datetime(frame.run_time, errors='coerce', utc=True)
            frame['_time'] = frame['_time'].fillna(fallback)
        frames.append(frame)
    if not frames:
        return []
    return pd.concat(frames, ignore_index=True).sort_values('_time', kind='stable').to_dict('records')


def estimate_spot_costs(account_root, quantities, prices, observed_at):
    root = Path(account_root)
    path = root / '账户信息' / 'spot_cost_estimate.json'
    fresh = not path.exists()
    state = {'version': 1, 'assets': {}, 'orders': {}} if fresh else json.loads(path.read_text(encoding='utf-8'))
    if state.get('version') != 1 or not isinstance(state.get('assets'), dict) or not isinstance(state.get('orders'), dict):
        raise ValueError('成本估算状态格式不支持')
    assets, seen = state['assets'], state['orders']
    for value in assets.values():
        if _number(value['quantity']) is None or _number(value['average']) is None or value['quantity'] < 0 or value['average'] < 0:
            raise ValueError('成本估算状态数值无效')
    for row in _orders(root):
        symbol, side, identity = str(row['symbol']), str(row['side']).upper(), row['client_order_id']
        qty, quote = _number(row['executed_qty']), _number(row['quote_qty'])
        if pd.isna(identity) or not str(identity).strip() or side not in ('BUY', 'SELL') or qty is None or quote is None or qty <= 0 or quote <= 0:
            continue
        key = json.dumps([symbol, side, str(identity)], ensure_ascii=False)
        previous = seen.get(key, [0., 0.])
        if qty <= previous[0]:
            continue  # Duplicate or older cumulative partial-fill response.
        dq, dv = qty - previous[0], quote - previous[1]
        if dv <= 0:
            raise ValueError('成本估算累计成交金额无效')
        seen[key] = [qty, quote]
        if fresh:
            continue  # First observation already contains these old fills.
        item = assets.setdefault(symbol, dict(quantity=0., average=0., since=str(observed_at), status='成交估算'))
        old = item['quantity']
        if side == 'BUY':
            item['average'] = (old * item['average'] + dv) / (old + dq)
            item['quantity'] = old + dq
        else:
            item['quantity'] = max(0., old - dq)
            if dq > old + max(1e-10, old * 1e-6):
                item['status'] = '估算重置'
            if item['quantity'] == 0:
                item['average'] = 0.
                item['since'] = str(observed_at)
                item['status'] = '成交估算'
    result = {}
    for symbol in set(assets) | set(quantities):
        qty, price = _number(quantities.get(symbol, 0.)), _number(prices.get(symbol))
        if qty is None or qty < 0:
            assets.pop(symbol, None)
            result[symbol] = dict(avg_price=None, cost_status='负持仓未估算')
            continue
        if qty == 0:
            assets.pop(symbol, None)
            result[symbol] = dict(avg_price=None, cost_status='无持仓')
            continue
        item = assets.get(symbol)
        mismatch = item is not None and not math.isclose(qty, item['quantity'], rel_tol=1e-6, abs_tol=1e-10)
        if item is None or mismatch:
            if price is None or price <= 0:
                result[symbol] = dict(avg_price=None, cost_status='缺报价未估算')
                continue
            item = dict(quantity=qty, average=price, since=str(observed_at), status='估算重置' if mismatch else '期初估算')
            assets[symbol] = item
        else:
            item['quantity'] = qty
        result[symbol] = dict(avg_price=item['average'], cost_status=item['status'], cost_since=item['since'])
    state['observed_at'] = str(observed_at)
    atomic_save(path, lambda target: target.write_text(json.dumps(state, ensure_ascii=False, allow_nan=False), encoding='utf-8'))
    return result
