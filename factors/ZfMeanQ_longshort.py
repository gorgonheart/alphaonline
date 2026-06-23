import pandas as pd
import numpy as np


def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    df['振幅'] = (df['high'] - df['low']) / df['open']
    df['振幅均值'] = df['振幅'].rolling(n, min_periods=1).mean()
    df['近期振幅均值'] = df['振幅'].rolling(4, min_periods=1).mean()
   
    

    df[factor_name] = df['振幅均值'].rank(ascending=True, pct=True)-df['近期振幅均值'].rank(ascending=True, pct=True)

    return df
