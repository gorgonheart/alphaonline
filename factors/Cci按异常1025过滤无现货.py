#!/usr/bin/python3
# -*- coding: utf-8 -*-


import pandas as pd
import numpy as np

# def signal(*args):
#     df = args[0]
#     n = args[1]
#     factor_name = args[2]



#     df['tp'] = (df['high'] + df['low'] + df['close']) / 3
#     df['ma'] = df['tp'].rolling(window=n, min_periods=1).mean()
#     df['md'] = abs(df['tp'] - df['ma']).rolling(window=n, min_periods=1).mean()

#     df[factor_name] = (df['tp'] - df['ma']) / (df['md'] * 0.015)

#     return df

def signal_multi_params(df, param_list) -> dict:
    ret = dict()
    cond = (df['symbol_swap'] == '') | (df['symbol_spot'] == '')

    tp = (df['high'] + df['low'] + df['close']) / 3
    tpnmax = df['high']
    tpnmin = df['low']
    if (len(tp) >= 2):
        tp.iloc[0] = tp.iloc[1]
        tpnmax.iloc[0] = tpnmax.iloc[1]
        tpnmin.iloc[0] = tpnmin.iloc[1]

    tpn = tp[-55560:]
    tpnmax = tpnmax.rolling(2).max()[-55560:]
    tpnmin = tpnmin.rolling(2).min()[-55560:]

    for param in param_list:
        n = int(param)

        # 1. 检测异常值（使用Z-score方法）
        pctchange = (tpnmax - tpnmin)/tpnmin
        pctma = pctchange.rolling(n, min_periods=1).mean()
        pctstd = pctchange.rolling(n, min_periods=1).std()
        pctzscore = (pctchange - pctma) / pctstd
        threshold = 25
        is_anomaly = (np.abs(pctzscore) > threshold) & ((pctchange > 0.2) | (pctchange < -0.2))#

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

                sub_ma = sub_df.rolling(window=n, min_periods=1).mean()
                sub_md = abs(sub_df - sub_ma).rolling(window=n, min_periods=1).mean()*0.015

                subfactor.append((sub_df - sub_ma) / (sub_md))
            else:
                subfactor.append(pd.Series(np.full(sub_df.shape[0], np.nan)))

        factor = pd.concat(subfactor,ignore_index=True)


        if (len(factor) != len(tp)):
            factor = pd.concat([pd.Series([np.nan] * (len(tp) - len(factor) )), factor])


        factor[cond] = np.nan
        ret[str(param)] = factor



    return ret