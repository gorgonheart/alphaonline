# -*- coding: utf-8 -*-
"""
中性策略框架 | 邢不行 | 2024分享会
author: 邢不行
微信: xbx6660
"""
import pandas as pd
import numpy as np

# def signal(*args):
#     df = args[0]
#     n = args[1]
#     factor_name = args[2]

#     source_cls = df.columns.tolist()

#     df['tp'] = (df['close'] + df['high'] + df['low']) / 3
#     df['tp2'] = df['tp'].ewm(span=n, adjust=False).mean()
#     df['diff'] = df['tp'] - df['tp2']
#     df['min'] = df['diff'].rolling(n, min_periods=1).min()
#     df['max'] = df['diff'].rolling(n, min_periods=1).max()

#     df[factor_name] = (df['diff'] - df['min']) / (df['max'] - df['min'])

#     df.drop(columns=list(set(df.columns.values).difference(set(source_cls + [factor_name]))), inplace=True)

#     return df


def signal_multi_params(df, param_list) -> dict:
    tp = (df['close'] + df['high'] + df['low']) / 3
    tpnmax = df['high']
    tpnmin = df['low']
    if (len(tp) >= 2):
        tp.iloc[0] = tp.iloc[1]
        tpnmax.iloc[0] = tpnmax.iloc[1]
        tpnmin.iloc[0] = tpnmin.iloc[1]


    tpn = tp[-52560:]
    tpnmax = tpnmax[-52560:]
    tpnmin = tpnmin[-52560:]

  
    ret = dict()
    for param in param_list:
        n = min(int(param), tpn.shape[0])
        # weights = np.concatenate(([1], 2**np.arange(n-1)))  
        # # tp_ma = tp.ewm(span=n, adjust=False).mean()

        # 1. 检测异常值（使用Z-score方法）
        pctchange = tpn.pct_change()
        pctma = pctchange.rolling(n, min_periods=1).mean()
        pctstd = pctchange.rolling(n, min_periods=1).std()
        pctzscore = (pctchange - pctma) / pctstd
        threshold = 25
        is_anomaly = (np.abs(pctzscore) > threshold) & ((pctchange > 0.2) | (pctchange < -0.5))#

        #将异常值替换为nan
        # print(df[is_anomaly].shape[0])
        anomaly_indices = tpn[is_anomaly].index.values.ravel()

        # close.loc[anomaly_indices, 'value'] = np.nan
        tpn.loc[anomaly_indices] = np.nan

        split_indices = np.where(is_anomaly)[0] + 1
        dfs = np.split(tpn, split_indices)

        subfactor = []
        for i, sub_df in enumerate(dfs):
            if sub_df.shape[0] > 168:
      

                sub_tp_ma = sub_df.ewm(span=n, adjust=False).mean()
                # tp_ma = tp.rolling(n, min_periods=1).apply(lambda x: x.ewm(n, adjust=False).mean().to_list()[-1])
                # print(pd.concat([tp_ma, tp], axis=1, keys=['tp_ma', 'tp']).head(1000))

                sub_diff = (sub_df - sub_tp_ma)
                sub_diff_min = sub_diff.rolling(n, min_periods=167).min()
                sub_diff_max = sub_diff.rolling(n, min_periods=167).max()

            
                subfactor.append((sub_diff - sub_diff_min) / (sub_diff_max - sub_diff_min))
            else:
                subfactor.append(pd.Series(np.full(sub_df.shape[0], np.nan)))
        


    #补长对齐df以免计算错误
        factor = pd.concat(subfactor,ignore_index=True)


        if (len(factor) != len(tpn)):
            factor = pd.concat([pd.Series([np.nan] * (len(factor) - len(tpn))), factor])

        max_high = tpnmax.rolling(26*168, min_periods=1).max()
        min_low = tpnmin.rolling(26*168, min_periods=1).min()
        factor2 = (tpn - min_low) / (max_high - min_low)

        ret[str(param)] = factor + factor2
    return ret
