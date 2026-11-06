"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import os
from tools.statistics_runtime import atomic_save, temporary_path
from tools.statistics_format import format_amount, format_time, format_frame, format_percent
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import dataframe_image as dfi
import numpy as np
import pandas as pd

from core.model.account_config import AccountConfig
from core.utils.dingding import send_wechat_work_msg, send_wechat_work_img
from core.utils.path_kit import get_file_path, get_folder_path

data_path = get_folder_path('data', as_path_type=True)


def calc_spot_position(spot_position, account_name, spot_last_price):
    """Market value plus independent fee-exclusive moving-average estimates."""
    if spot_position.empty:
        # Still consume fills and close estimates after a complete liquidation.
        try:
            from tools.spot_cost_estimate import estimate_spot_costs
            estimate_spot_costs(data_path / account_name, {}, {}, datetime.now())
        except Exception as error:
            print(f'{account_name}: 现货成本估算不可用: {error}')
        return pd.DataFrame()
    source = spot_position.set_index('symbol') if 'symbol' in spot_position else spot_position.copy()
    result = pd.DataFrame(index=source.index.rename('symbol'))
    result['pos_amount'] = pd.to_numeric(source['当前持仓量'], errors='raise')
    result['side'] = np.sign(result['pos_amount'])
    prices = pd.Series(spot_last_price, dtype=float).reindex(result.index)
    result['cur_price'] = prices.where(np.isfinite(prices) & (prices > 0))
    result['pos_u'] = result['pos_amount'] * result['cur_price']
    result['market_value'] = result['pos_u']
    result['change'] = np.nan
    result['pnl_u'] = np.nan
    result['avg_price'] = np.nan
    result['cost_status'] = '待对账'
    try:
        from tools.spot_cost_estimate import estimate_spot_costs
        costs = estimate_spot_costs(data_path / account_name, result.pos_amount.to_dict(),
                                    result.cur_price.to_dict(), datetime.now())
        result['avg_price'] = pd.Series({s: c['avg_price'] for s, c in costs.items()}, dtype=float).reindex(result.index)
        result['cost_status'] = pd.Series({s: c['cost_status'] for s, c in costs.items()}).reindex(result.index)
        result['cost_since'] = pd.to_datetime(pd.Series({s: c.get('cost_since') for s, c in costs.items()}).reindex(result.index))
        valid = (result.pos_amount > 0) & (result.avg_price > 0)
        result.loc[valid, 'change'] = result.loc[valid, 'cur_price'] / result.loc[valid, 'avg_price'] - 1
        result.loc[valid, 'pnl_u'] = result.loc[valid, 'pos_amount'] * (result.loc[valid, 'cur_price'] - result.loc[valid, 'avg_price'])
        result['change'] = result['change'].map(format_percent)
    except Exception as error:
        result['cost_status'] = '估算不可用'
        print(f'{account_name}: 现货成本估算不可用: {error}')
    return result


def calc_swap_position(swap_position):
    """
    发送现货、合约持仓信息
    :param swap_position: 合约持仓
    :return:
              side  change    pos_u   pnl_u  avg_price  cur_price
    symbol
    ARDRU     1  59.24%   419.98  248.81     0.0515     0.0820
    BTSU      1  36.90%   184.30   68.00     0.0071     0.0097
    GLMU      1  26.67%   691.78  184.49     0.1466     0.1857
    """
    swap_send_df = pd.DataFrame()
    # 如果存在合约持仓
    if not swap_position.empty:
        # =整理合约持仓数据
        swap_send_df = swap_position.set_index('symbol') if 'symbol' in swap_position else swap_position.copy()
        swap_send_df.index.name = 'symbol'
        mark = pd.to_numeric(swap_send_df['当前标记价格'], errors='raise')
        swap_send_df['当前标记价格'] = mark.where(np.isfinite(mark) & (mark > 0))
        swap_send_df['side'] = swap_send_df['当前持仓量'].apply(
            lambda x: 1 if float(x) > 0 else (-1 if float(x) < 0 else 0))  # 取出方向
        swap_send_df['change'] = (swap_send_df['当前标记价格'] / swap_send_df['均价'] - 1) * swap_send_df[
            'side']  # 计算涨跌幅
        swap_send_df['pos_u'] = (swap_send_df['notional'] if 'notional' in swap_send_df
                                else swap_send_df['当前持仓量'] * swap_send_df['当前标记价格'])
        swap_send_df.rename(columns={'均价': 'avg_price', '当前持仓量': 'pos_amount', '持仓盈亏': 'pnl_u', '当前标记价格': 'cur_price'},
                            inplace=True)  # 修改列名
        swap_send_df = swap_send_df[['side', 'change', 'pos_u', 'pnl_u', 'avg_price', 'cur_price', 'pos_amount']]
        swap_send_df.sort_values(['side', 'change'], ascending=[True, False], inplace=True)  # 以涨跌幅排序
        swap_send_df['change'] = swap_send_df['change'].transform(
            lambda x: f'{x * 100:.2f}%' if str(x) != 'nan' else x)  # 最后将数据转成百分比

        swap_send_df['market_value'] = swap_send_df['pos_u']

    return swap_send_df


def save_send_pos_info(timestamp_key, pos_df, account_name, pos_type='spot'):
    pos_pkl_path = get_file_path(data_path, account_name, '账户信息', f'pos_{pos_type}.pkl', as_path_type=True)
    if pos_pkl_path.exists():
        pos_pkl = pd.read_pickle(pos_pkl_path)
    else:
        pos_pkl = {}
    
    # 添加新数据
    pos_pkl[timestamp_key] = pos_df
    
    # 清理7天前的数据（基于传入的timestamp_key）
    seven_days_ms = 7 * 24 * 60 * 60 * 1000  # 7天的毫秒数
    cutoff_timestamp = timestamp_key - seven_days_ms
    
    # 删除超过7天的数据
    keys_to_remove = [key for key in pos_pkl.keys() if key < cutoff_timestamp]
    for key in keys_to_remove:
        del pos_pkl[key]
    
    # 保存数据
    atomic_save(pos_pkl_path, lambda path: pd.to_pickle(pos_pkl, path))
    
    # 输出清理信息
    if keys_to_remove:
        print(f"清理了{len(keys_to_remove)}条超过7天的{pos_type}持仓记录")
    return pos_pkl


def save_position_snapshot(account_config: AccountConfig, run_time):
    """Pre-order snapshot. Preserve precision; replace all rows of a repeated round."""
    observed_at = datetime.now()
    spot = account_config.spot_position
    prices = account_config.bn.get_spot_ticker_price_series() if not spot.empty else pd.Series(dtype=float)
    frames = {'spot': calc_spot_position(spot, account_config.name, prices),
              'swap': calc_swap_position(account_config.swap_position)}
    for market, frame in frames.items():
        frame = frame.reset_index() if not frame.empty else pd.DataFrame({'symbol': ['EMPTY'], 'market_value': [0.], 'pos_amount': [0.]})
        frame['timestamp'] = run_time
        frame['observed_at'] = observed_at
        path = get_file_path(data_path, account_config.name, '账户信息', f'{market}_position_history.pkl', as_path_type=True)
        if path.exists():
            old = pd.read_pickle(path)
            old = old[(old['timestamp'] != run_time) & (old['timestamp'] >= run_time - timedelta(days=7))]
            frame = pd.concat([old, frame], ignore_index=True)
        atomic_save(path, lambda target: frame.to_pickle(target))


def get_historical_position(account_name, hours_ago, pos_type='spot', *, observed_at=None, history=None, tolerance_minutes=10):
    """No future baseline; require an observation within ten minutes before target."""
    if history is None:
        path = get_file_path(data_path, account_name, '账户信息', f'{pos_type}_position_history.pkl', as_path_type=True)
        history = pd.read_pickle(path) if path.exists() else pd.DataFrame()
    if history.empty:
        return pd.DataFrame()
    target = pd.Timestamp(observed_at or datetime.now()) - pd.Timedelta(hours=hours_ago)
    times = pd.to_datetime(history['observed_at'] if 'observed_at' in history else history['timestamp'])
    eligible = times[(times <= target) & (times >= target-pd.Timedelta(minutes=tolerance_minutes))]
    if eligible.empty:
        return pd.DataFrame()
    actual = eligible.max()
    result = history.loc[times == actual].copy()
    result = result.loc[result['symbol'] != 'EMPTY'].reset_index(drop=True)
    result.attrs.update(baseline_time=actual, valid_baseline=True)
    return result


def calc_position_pnl(current_pos, historical_pos, spot_last_price, pos_type='spot', total_equity=None):
    """Hypothetical price impact of unchanged opening holdings; NOT trading PnL."""
    if historical_pos.empty or 'market_value' not in historical_pos:
        return pd.DataFrame()
    hist = historical_pos.set_index('symbol') if 'symbol' in historical_pos else historical_pos
    hist = hist.loc[hist['market_value'].notna() & hist['pos_amount'].notna()]
    if hist.empty:
        return pd.DataFrame()
    current = current_pos.set_index('symbol') if 'symbol' in current_pos else current_pos
    prices = pd.Series(spot_last_price, dtype=float).reindex(hist.index)
    if not current.empty:
        prices = current['cur_price'].reindex(hist.index).combine_first(prices)
    prices = prices.where(np.isfinite(prices) & (prices > 0))
    impact = hist['pos_amount'] * (prices - hist['cur_price'])
    result = pd.DataFrame({'historical_pos_u': hist['market_value'], 'historical_price': hist['cur_price'],
        'current_price': prices, 'position_pnl': impact, 'total_pnl': impact, 'trade_pnl': np.nan,
        'side': hist['side'], 'type': pos_type})
    result['current_pos_u'] = current['market_value'].reindex(hist.index, fill_value=0) if not current.empty else 0.
    result['price_change'] = impact / total_equity * 100 if total_equity and total_equity > 0 else np.nan
    return result.rename_axis('symbol').reset_index()


def calculate_and_send_pnl_report(account_config: AccountConfig, spot_position, swap_position, spot_last_price,
                                  me_conf=None, *, observed_at=None, run_time=None, current_frames=None,
                                  strategy_maps=None, total_equity=None, notify=True, history_cache=None,
                                  snapshot_source=None):
    """Optional estimate. Two history reads per round, persistence before notification."""
    observed_at = observed_at or datetime.now()
    run_time = run_time or observed_at
    frames = current_frames or (calc_spot_position(spot_position, account_config.name, spot_last_price), calc_swap_position(swap_position))
    histories = {}
    for market in ('spot', 'swap'):
        if history_cache is not None:
            from tools.statistics_snapshot import position_history
            histories[market] = position_history(history_cache[market], source=snapshot_source,
                                                 offset_minute=account_config.hour_offset_minute, observed_at=observed_at)
        else:
            path = get_file_path(data_path, account_config.name, '账户信息', f'{market}_position_history.pkl', as_path_type=True)
            histories[market] = pd.read_pickle(path) if path.exists() else pd.DataFrame()
    baselines = {(hours, market): get_historical_position(account_config.name, hours, market,
                 observed_at=observed_at, history=histories[market])
                 for hours in (1, 24) for market in ('spot', 'swap')}
    def required_symbols(market):
        symbols = set()
        for hours in (1, 24):
            baseline = baselines[hours, market]
            if not baseline.empty and 'market_value' in baseline:
                symbols.update(baseline.loc[baseline.market_value.notna(), 'symbol'])
        return symbols
    # Query closed symbols only for usable baselines, never all seven days of history.
    missing_swap = bool(required_symbols('swap') - set(frames[1].index))
    swap_prices = account_config.bn.get_swap_ticker_price_series() if missing_swap else pd.Series(dtype=float)
    if required_symbols('spot') - set(spot_last_price.index):
        spot_last_price = account_config.bn.get_spot_ticker_price_series()
    record = {'kind': 'opening_position_price_impact', 'observed_at': observed_at, 'run_time': run_time}
    lines = [f'【期初持仓价格影响估算】{account_config.name}', f'观察终点：{format_time(observed_at)}']
    for hours in (1, 24):
        results, intervals = [], {}
        for market, current, prices in zip(('spot', 'swap'), frames, (spot_last_price, swap_prices)):
            baseline = baselines[hours, market]
            result = calc_position_pnl(current, baseline, prices, market, total_equity)
            valid = baseline.attrs.get('valid_baseline', False)
            # Empty known holdings are valid; legacy non-empty rows cannot be valued reliably.
            usable = valid and (baseline.empty or (all(c in baseline for c in ('market_value', 'pos_amount', 'cur_price')) and baseline[['market_value', 'pos_amount', 'cur_price']].notna().all().all()))
            intervals[market] = dict(baseline_time=baseline.attrs.get('baseline_time'), usable=usable)
            if not usable:
                lines.append(f'{hours}h {market}: 历史不足或旧格式未核对')
            elif baseline.empty:
                lines.append(f'{hours}h {market}: 期初空仓')
            elif result['total_pnl'].isna().any():
                lines.append(f'{hours}h {market}: 缺价格，合计未知')
            else:
                lines.append(f'{hours}h {market}: {format_amount(result.total_pnl.sum())} U；实际起点 {format_time(intervals[market]["baseline_time"])}')
            if not result.empty:
                mapping = (strategy_maps or ({}, {}))[0 if market == 'spot' else 1]
                result['strategy'] = result['symbol'].map(mapping).fillna('未匹配')
                ranked = result.dropna(subset=['total_pnl']).sort_values('total_pnl', ascending=False)
                top = pd.concat([ranked.loc[ranked.total_pnl > 0].head(5), ranked.loc[ranked.total_pnl < 0].tail(5)])
                for row in top.itertuples():
                    lines.append(f'  {row.symbol}: {format_amount(row.total_pnl, signed=True)} U [{row.strategy}]')
            results.extend(result.to_dict('records'))
        record[f'{hours}h'] = results
        record[f'{hours}h_status'] = intervals
    path = get_file_path(data_path, account_config.name, '账户信息', 'pnl_history.pkl', as_path_type=True)
    saved = pd.read_pickle(path) if path.exists() else {}
    key = int(run_time.timestamp()*1000)
    saved = {k: v for k, v in saved.items() if k >= key-7*86400000}
    saved[key] = record
    atomic_save(path, lambda target: pd.to_pickle(saved, target))
    if notify:
        send_wechat_work_msg('\n'.join(lines), account_config.wechat_webhook_url)
    return record


def send_img_for_dataframe(dataframe, wechat_webhook_url):
    try:
        display = format_frame(dataframe)
        with temporary_path(data_path, '.png') as path:
            dfi.export(display, str(path), table_conversion='matplotlib', max_cols=-1, max_rows=-1)
            send_wechat_work_img(str(path), wechat_webhook_url)
    except Exception as error:
        print(f'统计持仓图失败: {error}')


def _join_strategy_names(items, max_display):
    """将多个策略名拼接为展示串，超过 max_display 个时省略尾部并标注剩余数量。"""
    items = list(items)
    if len(items) <= max_display:
        return ', '.join(items)
    head = ', '.join(items[:max_display])
    return f"{head} (+{len(items) - max_display} more)"


def build_symbol_strategy_columns(me_conf, max_strategy_display=5):
    """
    遍历 me_conf.factory.config_list，逐个读取每个 BacktestConfig 的
    最新持仓周期选币结果.pkl，构建 symbol → "父策略名(L), 父策略名(S)" 映射。
    用于在持仓图片中追加 "strategy" 列。

    :param me_conf: MultiEquityBacktestConfig，提供 factory.config_list 与 factory.result_folder
    :param max_strategy_display: 最多显示多个策略名称，超过显示省略
    :return: (spot_map, swap_map) 两个 dict；未命中的 symbol 不在返回值中，由调用方决定显示
    """
    def _format(items):
        return _join_strategy_names(items, max_strategy_display)

    active_idx_set = None
    try:
        ratios_df = pd.read_csv(me_conf.factory.result_folder / '仓位比例.csv', index_col=0)
        last_row = ratios_df.iloc[-1]
        active_idx_set = {
            i for i in range(len(me_conf.factory.config_list))
            if abs(float(last_row.get(str(i), 0))) > 1e-12
        }
    except BaseException:
        print('[策略名称列] 读取 仓位比例.csv 失败，按全部 conf 活跃处理:')
        print(traceback.format_exc())

    spot_parts = []
    swap_parts = []
    for idx, conf in enumerate(me_conf.factory.config_list):
        if active_idx_set is not None and idx not in active_idx_set:
            continue
        p = conf.get_result_folder() / '最新持仓周期选币结果.pkl'
        if not p.exists():
            continue
        try:
            df_i = pd.read_pickle(p)
        except BaseException:
            print(f'[策略名称列] 读取 {p} 失败:')
            print(traceback.format_exc())
            continue
        if df_i.empty:
            continue
        df_i = df_i.copy()
        df_i['symbol_spot'] = df_i['symbol_spot'].astype(object).fillna('').astype(str)
        df_i['symbol_swap'] = df_i['symbol_swap'].astype(object).fillna('').astype(str)
        df_i['parent_dir'] = np.char.add(conf.name, np.where(df_i['方向'] == 1, '(L)', '(S)'))

        spot_part = df_i.loc[df_i['symbol_spot'] != '', ['symbol_spot', 'parent_dir']]
        if not spot_part.empty:
            spot_parts.append(spot_part)
        swap_part = df_i.loc[df_i['symbol_swap'] != '', ['symbol_swap', 'parent_dir']]
        if not swap_part.empty:
            swap_parts.append(swap_part)

    if spot_parts:
        spot_df = pd.concat(spot_parts, ignore_index=True).drop_duplicates()
        spot_df = spot_df.sort_values(['symbol_spot', 'parent_dir'])
        spot_map = spot_df.groupby('symbol_spot')['parent_dir'].apply(_format).to_dict()
    else:
        spot_map = {}

    if swap_parts:
        swap_df = pd.concat(swap_parts, ignore_index=True).drop_duplicates()
        swap_df = swap_df.sort_values(['symbol_swap', 'parent_dir'])
        swap_map = swap_df.groupby('symbol_swap')['parent_dir'].apply(_format).to_dict()
    else:
        swap_map = {}

    return spot_map, swap_map


def build_recent_strategy_backfill_map(account_name, hours=24, max_strategy_display=5):
    """
    从最近 hours 小时的选币结果存档构建 symbol → "父策略名(L/S)" 映射，
    用于回填「当前持仓周期已轮出/平仓、但近 hours 小时内持有过」的币种归属。

    背景：当 hold_period=1h 每小时换币时，持仓盈亏报告的 24h 统计里会出现
    当前周期选币结果中不存在的币种，build_symbol_strategy_columns 无法识别其
    归属（显示空）。本函数从按小时归档的选币快照里回溯补齐。

    数据来源：data/{account}/select_coin/{YYYY-MM-DD_HH}.pkl
              （save_final_select_results 每小时写入，保留 72h）

    仅识别带 parent_name 列的存档记录；更早格式的存档无此列、对应币种不回填
    （存档仅保留 72h，会随时间自愈）。

    :return: (spot_map, swap_map)，未命中的 symbol 不在返回值中
    """
    spot_map, swap_map = {}, {}
    try:
        dir_path = data_path / account_name / 'select_coin'
        if not dir_path.exists():
            return spot_map, swap_map

        # 以整点为界向前取 hours 小时的存档文件
        cutoff = (datetime.now() - timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)
        parts = []
        for f in dir_path.glob('*.pkl'):
            try:
                file_time = datetime.strptime(f.stem, '%Y-%m-%d_%H')
            except ValueError:
                continue
            if file_time < cutoff:
                continue
            try:
                df_i = pd.read_pickle(f)
            except BaseException:
                continue
            if df_i.empty or 'parent_name' not in df_i.columns or '方向' not in df_i.columns:
                continue
            parts.append(df_i)

        if not parts:
            return spot_map, swap_map

        df_all = pd.concat(parts, ignore_index=True, copy=False).copy()
        df_all['parent_name'] = df_all['parent_name'].astype(object).fillna('').astype(str)
        df_all = df_all[df_all['parent_name'] != '']
        if df_all.empty:
            return spot_map, swap_map

        for col in ('symbol_spot', 'symbol_swap'):
            if col in df_all.columns:
                df_all[col] = df_all[col].astype(object).fillna('').astype(str)
            else:
                df_all[col] = ''

        df_all['parent_dir'] = np.char.add(
            df_all['parent_name'].to_numpy(dtype=str),
            np.where(df_all['方向'] == 1, '(L)', '(S)'),
        )

        spot_part = df_all.loc[df_all['symbol_spot'] != '', ['symbol_spot', 'parent_dir']].drop_duplicates()
        if not spot_part.empty:
            spot_part = spot_part.sort_values(['symbol_spot', 'parent_dir'])
            spot_map = spot_part.groupby('symbol_spot')['parent_dir'].apply(
                lambda s: _join_strategy_names(s, max_strategy_display)).to_dict()

        swap_part = df_all.loc[df_all['symbol_swap'] != '', ['symbol_swap', 'parent_dir']].drop_duplicates()
        if not swap_part.empty:
            swap_part = swap_part.sort_values(['symbol_swap', 'parent_dir'])
            swap_map = swap_part.groupby('symbol_swap')['parent_dir'].apply(
                lambda s: _join_strategy_names(s, max_strategy_display)).to_dict()
    except BaseException:
        print('[盈亏报告] 构建24h历史选币归属兜底映射失败:')
        print(traceback.format_exc())

    return spot_map, swap_map


# ====================================================================================================
# 最近 1h 成交额统计
# ====================================================================================================

def format_volume_message(spot_qv: float, swap_qv: float) -> str:
    """
    将 spot/swap 成交额格式化为推送消息行。

    :param spot_qv: 现货成交额 (USDT)
    :param swap_qv: 合约成交额 (USDT)
    :return: 整数金额展示
    """
    total = spot_qv + swap_qv
    return f'最近1h成交额:{format_amount(total)} (spot:{format_amount(spot_qv)}, swap:{format_amount(swap_qv)})'


_RECORD_COLUMNS = [
    'run_time', 'exec_time', 'account_name', 'symbol_type', 'symbol',
    'side', 'trade_mode', 'orig_qty', 'executed_qty', 'quote_qty',
    'avg_price', 'order_status', 'client_order_id',
]


def build_order_records(success_orders, orders_df, symbol_type, run_time, account_name):
    """
    将交易所成交回执列表归一化为 13 字段 DataFrame。

    spot/swap 字段名差异：
      - spot: cummulativeQuoteQty / transactTime
      - swap: cumQuote / updateTime + avgPrice

    成交额 quote_qty 取值优先级：交易所回执 cumQuote/cummulativeQuoteQty →
    avg_price * executed_qty → 挂单 price * executed_qty。
    （Binance CM/UM 整合自 2026-06-30 起移除 U本位下单「即时响应」中的 cumQuote 字段，
    故不再以 cumQuote 是否存在作为有效性判据，改用 executed_qty 是否 > 0。）

    无效记录（空 dict / 缺关键字段 / 未成交 executed_qty<=0）会被跳过。
    """
    rows = []
    for order_res in success_orders:
        if not order_res or not isinstance(order_res, dict):
            continue
        symbol = order_res.get('symbol')
        if not symbol:
            continue
        status = order_res.get('status')
        if status not in ('FILLED', 'PARTIALLY_FILLED'):
            continue

        if symbol_type == 'spot':
            quote_qty_raw = order_res.get('cummulativeQuoteQty')
            exec_time_raw = order_res.get('transactTime')
            avg_price_raw = None
        else:
            quote_qty_raw = order_res.get('cumQuote')
            exec_time_raw = order_res.get('updateTime')
            avg_price_raw = order_res.get('avgPrice')

        executed_qty = float(order_res.get('executedQty', 0) or 0)
        orig_qty = float(order_res.get('origQty', 0) or 0)

        # 无实际成交量直接跳过。
        # 注意：不再用 quote_qty_raw(cumQuote) 是否存在作判据——
        # CM/UM 整合自 2026-06-30 起移除 U本位下单即时响应中的 cumQuote 字段。
        if executed_qty <= 0:
            continue

        # 成交均价：优先回执 avgPrice，其次 cumQuote/executed_qty，最后退化为挂单 price
        if avg_price_raw is not None and float(avg_price_raw) > 0:
            avg_price = float(avg_price_raw)
        elif quote_qty_raw is not None and float(quote_qty_raw) > 0:
            avg_price = float(quote_qty_raw) / executed_qty
        else:
            avg_price = float(order_res.get('price', 0) or 0)

        # 成交额：优先交易所回执 cumQuote/cummulativeQuoteQty（须 > 0）；
        # cumQuote 缺失(普通账户，键为 None)或返回 0(统一账户/PM 即时回执 cumQuote、avgPrice
        # 均为 '0.00000000') 时，用 avg_price * executed_qty 兜底，避免采信 0 导致成交额恒为 0
        if quote_qty_raw is not None and float(quote_qty_raw) > 0:
            quote_qty = float(quote_qty_raw)
        else:
            quote_qty = avg_price * executed_qty

        if exec_time_raw is None:
            exec_time = pd.NaT
        else:
            exec_time = pd.to_datetime(int(exec_time_raw), unit='ms')

        if symbol in orders_df.index:
            trade_mode = orders_df.loc[symbol].get('交易模式', '')
            if isinstance(trade_mode, pd.Series):
                trade_mode = trade_mode.iloc[0]
        else:
            trade_mode = ''

        rows.append({
            'run_time': run_time,
            'exec_time': exec_time,
            'account_name': account_name,
            'symbol_type': symbol_type,
            'symbol': symbol,
            'side': order_res.get('side', ''),
            'trade_mode': trade_mode,
            'orig_qty': orig_qty,
            'executed_qty': executed_qty,
            'quote_qty': quote_qty,
            'avg_price': avg_price,
            'order_status': status,
            'client_order_id': order_res.get('clientOrderId', ''),
        })

    return pd.DataFrame(rows, columns=_RECORD_COLUMNS)


def save_order_records(success_spot_df, success_swap_df, run_time, account_name):
    """
    将 spot/swap 成交记录追加到 data/{account}/订单记录/order_history_{YYYYMM}.csv

    - 编码 gbk，与现有 equity.csv 一致
    - 文件不存在则建表头；存在则 append 不写表头
    - 任何 IO/编码异常都不上抛，只打 warning 日志，避免阻断主流程
    """
    try:
        frames = [df for df in (success_spot_df, success_swap_df) if df is not None and not df.empty]
        if not frames:
            return
        merged = pd.concat(frames, axis=0, ignore_index=True)
        if merged.empty:
            return

        merged['run_time'] = pd.to_datetime(merged['run_time']).dt.strftime('%Y-%m-%d %H:%M:%S')

        target_dir = data_path / account_name / '订单记录'
        target_dir.mkdir(parents=True, exist_ok=True)
        file_name = f'order_history_{run_time.strftime("%Y%m")}.csv'
        target_path = target_dir / file_name

        write_header = not target_path.exists()
        merged.to_csv(target_path, mode='a', header=write_header, index=False, encoding='gbk')
    except Exception as e:
        traceback.print_exc()
        print(f'[订单记录] save_order_records 失败：{e}（不阻断主流程）')


def compute_recent_volume(account_name, run_time, *, strict=False):
    """
    从订单记录 csv 中按 (account_name, run_time) 精确过滤，求 spot/swap 成交额。

    :return: (spot_qv, swap_qv) tuple[float, float]，文件缺失或无匹配返回 (0.0, 0.0)
    """
    try:
        file_path = data_path / account_name / '订单记录' / f'order_history_{run_time.strftime("%Y%m")}.csv'
        if not file_path.exists():
            if strict:
                raise FileNotFoundError(file_path)
            return 0.0, 0.0

        df = pd.read_csv(file_path, encoding='gbk', parse_dates=['run_time'])
        if df.empty:
            return 0.0, 0.0

        df['run_time'] = pd.to_datetime(df['run_time'], format='mixed', errors='coerce')

        mask = (df['account_name'] == account_name) & (df['run_time'] == run_time)
        if not mask.any():
            return 0.0, 0.0

        sub = df.loc[mask]
        spot_qv = float(sub.loc[sub['symbol_type'] == 'spot', 'quote_qty'].sum())
        swap_qv = float(sub.loc[sub['symbol_type'] == 'swap', 'quote_qty'].sum())
        return spot_qv, swap_qv
    except Exception as e:
        if strict:
            raise
        traceback.print_exc()
        print(f'[订单记录] compute_recent_volume 失败：{e}（返回 0, 0）')
        return 0.0, 0.0
