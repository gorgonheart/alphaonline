"""Cumulative settled funding, including the available inception history."""
import pandas as pd
def signal(df,n,col):
    df[col]=pd.to_numeric(df['funding_fee'],errors='coerce').rolling(int(n),min_periods=1).sum()
    return df
def signal_multi_params(df,params):
    x=pd.to_numeric(df['funding_fee'],errors='coerce')
    return {str(n):x.rolling(int(n),min_periods=1).sum() for n in params}
