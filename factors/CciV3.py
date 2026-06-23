 #!/usr/bin/python3
# -*- coding: utf-8 -*-

#!/usr/bin/python3
# -*- coding: utf-8 -*-

import pandas as pd
import numpy  as np

def ewm_fixed(arr, window, span=None, alpha=None, min_periods=None):
    """
    This function calculates the Exponential Weighted Moving Average (EWMA) of a given array.
    The function takes either a span or an alpha value as input.
    The span is the time period over which the EWMA is calculated.
    The alpha value is the smoothing factor for the EWMA calculation.
    The function returns the EWMA of the input array.
    :param arr: The input array for which the EWMA is to be calculated.
    :param window: The window size for the rolling calculation.
    :param span: The time period over which the EWMA is calculated.
    :param alpha: The smoothing factor for the EWMA calculation.
    :param min_periods: The minimum number of observations in window required to have a value.
    :return: The EWMA of the input array.
    """

    # Check if span or alpha is provided
    if span is not None:
        alpha = 2/(span+1)
    if span is None and alpha is None:
        raise ValueError('You must pass either span or alpha')
    if alpha is not None:
        if alpha < 0 or alpha > 1:
            raise ValueError('Alpha must be between 0 and 1')

    # Calculate EWMA
    y = arr.ewm(alpha=alpha, adjust=False).mean()
    # Calculate residual
    w = window
    residual = (1-alpha)**(w) * y + (alpha-1)*(1-alpha)**(w-1) * arr.shift(-1)
    residual = residual.shift(w).fillna(0)
    # Calculate EWMA fast
    ewm_fixed = y - residual
    if min_periods is not None:
        ewm_fixed[:min_periods-1] = np.nan
    return ewm_fixed


# def signal(*args):
#     #  Cci因子魔改的第三个版本，参考自《中性5期船队因子库》: https://bbs.quantclass.cn/thread/13472
#     df = args[0]
#     n = args[1]
#     factor_name = args[2]

#     oma = df['open'].ewm(span=n, adjust=False).mean()
#     hma = df['high'].ewm(span=n, adjust=False).mean()
#     lma = df['low'].ewm(span=n, adjust=False).mean()
#     cma = df['close'].ewm(span=n, adjust=False).mean()
#     tp = (oma + hma + lma + cma) / 4
#     ma = tp.ewm(span=n, adjust=False).mean()
#     md = (cma - ma).abs().ewm(span=n, adjust=False).mean()
#     df[factor_name] = (tp - ma) / md

#     return df


def signal_multi_params(df, param_list) -> dict:
    ret = dict()

    for param in param_list:
        n = int(param)

        oma = ewm_fixed(df['open'], 15000, span=n, min_periods=2)
        hma = ewm_fixed(df['high'], 15000, span=n, min_periods=2)
        lma = ewm_fixed(df['low'], 15000, span=n, min_periods=2)
        cma = ewm_fixed(df['close'], 15000, span=n, min_periods=2)


        tp = (oma + hma + lma + cma) / 4
        ma = ewm_fixed(tp, 15000, span=n, min_periods=2)
        md = ewm_fixed((cma - ma).abs(), 15000, span=n, min_periods=2)

        ret[str(param)] = (tp - ma) / md
    return ret