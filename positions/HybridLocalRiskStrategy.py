"""Opt-in S12 LOCAL wrapper. Never changes other members or core execution timing."""
import logging
from pathlib import Path
import numpy as np
import pandas as pd
from tools.local_risk_model import MEMBERS,COLUMNS,update,multipliers

def calc_ratio(equity_dfs,stg_conf):
    from positions import HybridRotationDriftStrategy as hybrid
    base=hybrid.calc_ratio(equity_dfs,stg_conf)
    p=stg_conf.params;mode=p.get('local_mode','shadow')
    if mode not in ('shadow','active'):raise ValueError('local_mode must be shadow or active')
    try:
        if len(equity_dfs)!=14 or base.shape[1]!=14 or stg_conf.hold_period!='1H':
            raise ValueError('LOCAL requires the original 14-member / 1H position interface')
        if stg_conf.rebalance_cap_step!=1 or stg_conf.symbol_ratio_limit:
            raise ValueError('Extra smoothing/limits require separate validation')
        expected=p['local_expected_names']
        actual=[c.name for c in stg_conf.strategy_cfg_list]
        if len(expected)!=14 or actual!=expected or '回撤阈值空头' not in actual[11]:
            raise ValueError('Member identity/order changed')
        for group in p.get('dynamic_groups',[]):
            indices=group.get('indices',group.get('strategy_indices',[])) if isinstance(group,dict) else group
            if 11 in indices:raise ValueError('S12 must remain outside dynamic groups')
        if 11 in p.get('rotation_indices',[0,1]):raise ValueError('S12 must not rotate')
        times=pd.DatetimeIndex(pd.to_datetime(base.index,utc=True))
        cols=[]
        for i in MEMBERS:
            df=equity_dfs[i]
            s=pd.Series(df['close'].to_numpy(),index=pd.to_datetime(df['candle_begin_time'],utc=True))
            if not s.index.is_unique:raise ValueError('Duplicate member times')
            cols.append(s.reindex(times).to_numpy())
        frame=pd.DataFrame(np.column_stack(cols),index=times,columns=COLUMNS)
        state_path=Path(p['local_state_path'])
        if not state_path.is_absolute():
            state_path=Path(__file__).resolve().parents[1]/state_path
        state=update(state_path,frame,p['local_identity'])
        m=multipliers(state,times)
        if not np.isfinite(m).all() or (m<.8-1e-12).any() or (m>1+1e-12).any():
            raise ValueError('Invalid LOCAL multiplier')
        logging.getLogger(__name__).warning('LOCAL %s identity=%s time=%s multiplier=%.8f',
            mode,p['local_identity'],times[-1],m[-1])
        if mode=='shadow':return base
        out=base.copy();out.iloc[:,11]=base.iloc[:,11].to_numpy()*m
        return out
    except Exception:
        if mode=='active':raise
        logging.getLogger(__name__).exception('LOCAL shadow update failed; baseline output unchanged')
        return base
