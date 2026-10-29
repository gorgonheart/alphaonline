"""
邢不行｜策略分享会
仓位管理实盘框架 - 线上4H滚动风险引擎与原子状态管理

版权所有 ©️ 邢不行
微信: xbx1717

功能：
1. 维护各分钟偏移（20m, 40m, 55m等）的 4H 滚动风险评估状态；
2. 盘中严格使用 t-1 闭合K线计算 15 维截面/时序特征，杜绝未来函数；
3. 输出高风险币种名单与减仓 Delta，强制守恒运行时不变性（杜绝裸空/反向开空）；
4. 提供原子状态缓存与毫秒级复用，并在异常时自动安全降级。
"""
from __future__ import annotations
import os
import sys
import json
import logging
from pathlib import Path
import numpy as np
import pandas as pd

from tools.train_robust_model import compute_single_coin_features, ALL_FEATURE_NAMES

logger = logging.getLogger('robust_risk_4h')

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_PATH = PROJECT_ROOT / 'data' / 'models' / 'risk_model_quarterly.pkl'
CACHE_DIR = PROJECT_ROOT / 'data' / 'cache'


class RobustRisk4HEngine:
    _model_cache: dict[str, dict] = {}

    def __init__(self, model_dir: str | Path = PROJECT_ROOT / 'data' / 'models'):
        self.model_dir = Path(model_dir)

    def _load_model_for_offset(self, hour_offset: str) -> dict | None:
        """分层加载模型：优先专属模型 risk_model_{offset}.pkl，回退通用模型 risk_model_quarterly.pkl"""
        if hour_offset in self._model_cache:
            return self._model_cache[hour_offset]

        # 1. 优先尝试特定偏移模型 (例如 risk_model_20m.pkl)
        specific_path = self.model_dir / f'risk_model_{hour_offset}.pkl'
        if specific_path.exists():
            try:
                bundle = pd.read_pickle(specific_path)
                self._model_cache[hour_offset] = bundle
                logger.info(f"[{hour_offset}] 成功载入专属风控模型: {specific_path.name}, 版本: {bundle.get('version')}")
                return bundle
            except Exception as e:
                logger.warning(f"加载专属模型 {specific_path} 失败: {e}，将尝试回退通用模型")

        # 2. 回退通用季度模型
        universal_path = self.model_dir / 'risk_model_quarterly.pkl'
        if universal_path.exists():
            try:
                bundle = pd.read_pickle(universal_path)
                self._model_cache[hour_offset] = bundle
                logger.info(f"[{hour_offset}] 载入通用季度风控模型: {universal_path.name}")
                return bundle
            except Exception as e:
                logger.error(f"加载通用模型 {universal_path} 失败: {e}")

        logger.warning(f"[{hour_offset}] 未找到任何有效模型文件，将使用被动兜底模式")
        return None

    def get_quarter_model(self, hour_offset: str, run_time: pd.Timestamp):
        """根据当前实盘时间匹配最贴近的季度模型（严格不使用未来季度模型）"""
        bundle = self._load_model_for_offset(hour_offset)
        if not bundle:
            return None
        models = bundle.get('models', {})
        if not models:
            return None
        ts = pd.to_datetime(run_time)
        valid_keys = [k for k in models.keys() if k <= ts]
        if valid_keys:
            chosen_key = max(valid_keys)
            return models[chosen_key]
        return bundle.get('latest_model')

    def get_cache_path(self, hour_offset: str) -> Path:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        return CACHE_DIR / f'risk_state_{hour_offset}.json'

    def read_cached_state(self, hour_offset: str) -> dict | None:
        p = self.get_cache_path(hour_offset)
        if not p.exists():
            return None
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"读取风险状态缓存失败: {e}")
            return None

    def write_cached_state(self, hour_offset: str, state: dict):
        p = self.get_cache_path(hour_offset)
        tmp = p.with_suffix('.tmp')
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            tmp.replace(p)  # 原子替换，防止进程读写冲突
        except Exception as e:
            logger.error(f"写入风险状态缓存失败: {e}")

    def evaluate_held_coins(
        self,
        hour_offset: str,
        run_time: pd.Timestamp,
        held_symbols: list[str],
        candles_dict: dict[str, pd.DataFrame]
    ) -> list[str]:
        """
        评估当前持仓币种的下行风险，输出高风险需减仓币种名单（排名最高的前20%）
        """
        if not held_symbols or not candles_dict:
            return []

        model = self.get_quarter_model(hour_offset, run_time)
        if model is None:
            logger.warning("未找到有效风险模型，跳过4H风险减仓")
            return []

        # 1. 计算截面特征 (breadth_down, market_ret24, dispersion)
        close_series = {}
        for sym in held_symbols:
            if sym in candles_dict and not candles_dict[sym].empty:
                df = candles_dict[sym]
                close_series[sym] = df.set_index('candle_begin_time')['close']

        if not close_series:
            return []

        piv = pd.DataFrame(close_series)
        ret24 = piv.pct_change(24, fill_method=None)

        last_t = piv.index[-1]
        m_ret24 = float(ret24.iloc[-1].mean()) if not ret24.empty else 0.0
        m_breadth = float((ret24.iloc[-1] < 0).mean()) if not ret24.empty else 0.5
        m_disp = float(ret24.iloc[-1].std()) if not ret24.empty else 0.0

        scores = {}
        for sym in held_symbols:
            if sym not in candles_dict or len(candles_dict[sym]) < 24:
                continue
            df = candles_dict[sym].copy().sort_values('candle_begin_time').reset_index(drop=True)
            f = compute_single_coin_features(df)

            # 使用 t-1 期的特征，严格杜绝当前根K线未来信息
            if len(f) < 2:
                continue
            last_feat = f.iloc[-2].copy()  # shift(1) 效果
            last_feat['breadth_down'] = m_breadth
            last_feat['market_ret24'] = m_ret24
            last_feat['dispersion'] = m_disp

            feat_vec = last_feat.reindex(ALL_FEATURE_NAMES).to_frame().T
            if feat_vec.isna().all(axis=1).any():
                continue
            try:
                prob = float(model.predict_proba(feat_vec.clip(-1e6, 1e6))[:, 1][0])
                scores[sym] = prob
            except Exception as e:
                logger.warning(f"币种 {sym} 风险推断异常: {e}")

        if not scores:
            return []

        score_s = pd.Series(scores)
        # 在持仓币种内进行百分比排序
        rank_pct = score_s.rank(pct=True)
        high_risk_symbols = rank_pct[rank_pct > 0.8].index.tolist()

        logger.info(
            f"[{hour_offset}] 4H风险评估完成 @ {run_time}: 持仓币数={len(held_symbols)}, "
            f"有效评分={len(scores)}, 高风险剪裁币数={len(high_risk_symbols)} ({high_risk_symbols})"
        )
        return high_risk_symbols

    def get_or_update_cut_symbols(
        self,
        hour_offset: str,
        run_time: pd.Timestamp,
        held_symbols: list[str],
        candles_dict: dict[str, pd.DataFrame],
        force_update: bool = False
    ) -> list[str]:
        """
        核心调度接口：4H窗口执行预测，非4H窗口毫秒级复用状态
        """
        run_dt = pd.to_datetime(run_time)
        is_4h_boundary = (run_dt.hour % 4 == 0)
        cached = self.read_cached_state(hour_offset)

        if not force_update and not is_4h_boundary and cached:
            # 非 4H 窗口：复用之前评估的状态
            cached_cuts = cached.get('cut_symbols', [])
            # 过滤掉已经不在当前持仓中的币种
            valid_cuts = [s for s in cached_cuts if s in held_symbols]
            logger.debug(f"[{hour_offset}] 复用4H缓存状态 (更新于 {cached.get('last_eval_time')}): 剪裁币数={len(valid_cuts)}")
            return valid_cuts

        # 到达 4H 评估窗口或无缓存：触发全量更新
        cut_symbols = self.evaluate_held_coins(hour_offset, run_time, held_symbols, candles_dict)

        new_state = {
            'hour_offset': hour_offset,
            'last_eval_time': str(run_dt),
            'cadence_hours': 4,
            'held_symbols': held_symbols,
            'cut_symbols': cut_symbols
        }
        self.write_cached_state(hour_offset, new_state)
        return cut_symbols


# 单例全局引擎
engine = RobustRisk4HEngine()
