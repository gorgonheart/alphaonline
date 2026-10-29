"""
邢不行｜策略分享会
仓位管理实盘框架 - 4H稳健动量风险模型离线训练与增量重训流水线

版权所有 ©️ 邢不行
微信: xbx1717

功能：
1. 从历史行情数据（BMAC / preprocess-pkl）构建时序与截面 15 维风险特征；
2. 按照季度滚动训练 Logistic Regression 风险预警模型；
3. 输出持久化模型文件至 data/models/risk_model_quarterly.pkl，供线上 4H 实盘引擎调用。
"""
from __future__ import annotations
import os
import sys
import time
import json
import logging
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.linear_model import LogisticRegression

# 特征名规范定义
COIN_FEATURES = [
    'ret1', 'ret24', 'ret168', 'drawdown168', 'vol_ratio', 'downside168',
    'volume_ratio', 'log_volume24', 'amihud24', 'taker24', 'funding24', 'age_days'
]
MARKET_FEATURES = ['breadth_down', 'market_ret24', 'dispersion']
ALL_FEATURE_NAMES = COIN_FEATURES + MARKET_FEATURES

logger = logging.getLogger(__name__)


def compute_single_coin_features(df: pd.DataFrame) -> pd.DataFrame:
    """计算单个币种的12维时序特征（严格使用收盘行情，无未来函数）"""
    close = df['close']
    volume = df['volume']
    quote_volume = df['quote_volume']
    taker_buy_quote_volume = df.get('taker_buy_quote_asset_volume', quote_volume * 0.5)
    funding_rate = df.get('funding_rate', pd.Series(0.0, index=df.index))

    # 收益率与波动
    ret1 = close.pct_change(1, fill_method=None)
    ret24 = close.pct_change(24, fill_method=None)
    ret168 = close.pct_change(168, fill_method=None)

    max168 = close.rolling(168, min_periods=24).max()
    drawdown168 = (close - max168) / max168

    vol24 = ret1.rolling(24, min_periods=12).std()
    vol168 = ret1.rolling(168, min_periods=24).std()
    vol_ratio = vol24 / vol168.replace(0, np.nan)

    downside168 = ret1.clip(upper=0).pow(2).rolling(168, min_periods=24).mean().pow(0.5)

    # 成交量与资金费
    vol_mean24 = quote_volume.rolling(24, min_periods=12).mean()
    vol_mean168 = quote_volume.rolling(168, min_periods=24).mean()
    volume_ratio = vol_mean24 / vol_mean168.replace(0, np.nan)
    log_volume24 = np.log1p(vol_mean24.clip(lower=0))

    # Amihud流动性与主动买入比
    amihud24 = (ret1.abs() / quote_volume.replace(0, np.nan)).rolling(24, min_periods=12).mean()
    taker24 = (taker_buy_quote_volume / quote_volume.replace(0, np.nan)).rolling(24, min_periods=12).mean()
    funding24 = funding_rate.rolling(24, min_periods=12).mean()

    # 上市天数
    age_days = (pd.Series(range(len(df)), index=df.index) / 24.0)

    feat = pd.DataFrame({
        'ret1': ret1,
        'ret24': ret24,
        'ret168': ret168,
        'drawdown168': drawdown168,
        'vol_ratio': vol_ratio,
        'downside168': downside168,
        'volume_ratio': volume_ratio,
        'log_volume24': log_volume24,
        'amihud24': amihud24,
        'taker24': taker24,
        'funding24': funding24,
        'age_days': age_days
    }, index=df.index)

    return feat


def train_quarterly_models(obs_features_path: str | Path, output_model_path: str | Path):
    """
    基于预计算特征训练季度模型包
    obs_features_path: 包含历史观测特征的 DataFrame (包含 time, forward24, 及 15 维特征)
    output_model_path: 产出模型文件路径
    """
    obs = pd.read_pickle(obs_features_path)
    logger.info(f"加载训练样本: {len(obs)} 行，时间范围: {obs['time'].min()} -> {obs['time'].max()}")

    models = {}
    quarter_dates = pd.date_range('2021-07-01', obs['time'].max() + pd.Timedelta(days=90), freq='QS')

    for begin in quarter_dates:
        # 训练窗口：向前滚动 365 天，且截止到当前季度开始前 48 小时（严格防未来数据）
        train_mask = (
            (obs['time'] >= begin - pd.Timedelta(days=365)) &
            (obs['time'] < begin - pd.Timedelta(hours=48)) &
            obs['forward24'].notna()
        )
        sample = obs[train_mask]
        if sample['time'].nunique() < 120:
            continue

        X = sample[ALL_FEATURE_NAMES].clip(-1e6, 1e6)
        y = sample['forward24'].lt(-0.05).astype(int)

        # 稳健 Logistic Regression Pipeline
        pipe = make_pipeline(
            SimpleImputer(strategy='median'),
            StandardScaler(),
            LogisticRegression(C=0.1, max_iter=500, solver='lbfgs', random_state=42)
        )
        pipe.fit(X, y)
        models[begin] = pipe
        logger.info(f"季度模型完成训练: {begin.strftime('%Y-%m-%d')}, 样本量={len(sample)}, 正样本率={y.mean()*100:.2f}%")

    out_dir = Path(output_model_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    model_bundle = {
        'version': 'robust_4h_lr_universal_v1',
        'feature_names': ALL_FEATURE_NAMES,
        'models': models,
        'latest_quarter': max(models.keys()),
        'latest_model': models[max(models.keys())],
        'updated_at': pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M:%S')
    }
    pd.to_pickle(model_bundle, output_model_path)
    logger.info(f"✅ 通用模型包已持久化: {output_model_path}, 包含 {len(models)} 个季度模型")
    return model_bundle


def train_offset_model(offset: str, output_model_path: str | Path | None = None):
    """从特定分钟偏移数据源（20m, 40m, 55m等）抽取特征并训练专属模型"""
    t0 = time.time()
    logger.info(f"========== 开始为 [{offset}] 构建专属特征并训练专属模型 ==========")
    raw_path = rf'D:\data\coin-binance-spot-swap-preprocess-pkl-1h-{offset}\preprocess-pkl\swap_dict.pkl'
    piv_path = rf'D:\data\coin-binance-spot-swap-preprocess-pkl-1h-{offset}\preprocess-pkl\market_pivot_swap.pkl'
    if not os.path.exists(raw_path) or not os.path.exists(piv_path):
        logger.error(f"[{offset}] 数据路径不存在: {raw_path}")
        return None

    raw = pd.read_pickle(raw_path)
    piv = pd.read_pickle(piv_path)
    close = piv['close']
    ret24 = close.pct_change(24, fill_method=None)
    market_ret24 = ret24.mean(axis=1)
    breadth_down = (ret24 < 0).mean(axis=1)
    dispersion = ret24.std(axis=1)

    child_root = rf'E:\quant\position-mgmt_v2.3.0_{offset}\results\children\OV1028B_{offset}_ORIGINAL'
    s5_ratio_path = os.path.join(child_root, 'S5-动量实盘多头', 'df_swap_ratio.pkl')
    if not os.path.exists(s5_ratio_path):
        logger.error(f"[{offset}] S5选币缓存未找到: {s5_ratio_path}")
        return None

    s5_swap = pd.read_pickle(s5_ratio_path)
    idx = s5_swap.index
    symbols = s5_swap.columns
    held_symbols = [s for s in symbols if (s5_swap[s] > 0).any() and s in raw]

    days = idx[idx.hour == 0]
    observations = []

    for sym in held_symbols:
        df = raw[sym].sort_values('candle_begin_time').drop_duplicates('candle_begin_time')
        if len(df) < 168:
            continue
        f = compute_single_coin_features(df)
        f.index = pd.DatetimeIndex(df['candle_begin_time'])
        f = f.reindex(idx).shift(1)  # 严格防未来数据

        pr = df.set_index('candle_begin_time')['close'].reindex(idx)
        exec_p = df.set_index('candle_begin_time').get('avg_price_1m', pr).reindex(idx)
        forward = pr.shift(-24) / exec_p.shift(-1) - 1

        selected = s5_swap.loc[days, sym] > 0
        if not selected.any():
            continue

        obs = f.loc[days].loc[selected, COIN_FEATURES].copy()
        obs['symbol'] = sym
        obs['time'] = obs.index
        obs['forward24'] = forward.reindex(obs.index)
        obs['breadth_down'] = breadth_down.reindex(obs.index)
        obs['market_ret24'] = market_ret24.reindex(obs.index)
        obs['dispersion'] = dispersion.reindex(obs.index)
        observations.append(obs.reset_index(drop=True))

    all_obs = pd.concat(observations, ignore_index=True)
    logger.info(f"[{offset}] 特征构建完成: {len(all_obs)} 个有效持仓样本，耗时 {time.time() - t0:.2f}s")

    models = {}
    quarter_dates = pd.date_range('2021-07-01', all_obs['time'].max() + pd.Timedelta(days=90), freq='QS')
    for begin in quarter_dates:
        train_mask = (
            (all_obs['time'] >= begin - pd.Timedelta(days=365)) &
            (all_obs['time'] < begin - pd.Timedelta(hours=48)) &
            all_obs['forward24'].notna()
        )
        sample = all_obs[train_mask]
        if sample['time'].nunique() < 120:
            continue

        X = sample[ALL_FEATURE_NAMES].clip(-1e6, 1e6)
        y = sample['forward24'].lt(-0.05).astype(int)

        pipe = make_pipeline(
            SimpleImputer(strategy='median'),
            StandardScaler(),
            LogisticRegression(C=0.1, max_iter=500, solver='lbfgs', random_state=42)
        )
        pipe.fit(X, y)
        models[begin] = pipe

    if output_model_path is None:
        output_model_path = rf'D:\alphaonline\data\models\risk_model_{offset}.pkl'

    out_dir = Path(output_model_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    bundle = {
        'version': f'robust_4h_lr_{offset}_v1',
        'hour_offset': offset,
        'feature_names': ALL_FEATURE_NAMES,
        'models': models,
        'latest_quarter': max(models.keys()),
        'latest_model': models[max(models.keys())],
        'updated_at': pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M:%S'),
        'sample_count': len(all_obs)
    }
    pd.to_pickle(bundle, output_model_path)
    logger.info(f"✅ [{offset}] 专属模型已保存: {output_model_path}, 包含 {len(models)} 个季度模型, 总耗时 {time.time() - t0:.2f}s")
    return bundle


if __name__ == '__main__':
    import argparse
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    parser = argparse.ArgumentParser(description="4H 稳健风控模型训练入口")
    parser.add_argument('--offset', type=str, default='all', choices=['all', 'universal', '20m', '40m', '55m'], help="指定训练偏移或全部")
    args = parser.parse_args()

    default_src = Path(r'E:\quant\position-mgmt_v2.3.0\research_20261028_october_validation_v2\inputs\FEATURES.pkl')
    default_dst = Path(r'D:\alphaonline\data\models\risk_model_quarterly.pkl')

    if args.offset in ('all', 'universal') and default_src.exists():
        train_quarterly_models(default_src, default_dst)

    if args.offset in ('all', '20m'):
        train_offset_model('20m')
    if args.offset in ('all', '40m'):
        train_offset_model('40m')
    if args.offset in ('all', '55m'):
        train_offset_model('55m')
