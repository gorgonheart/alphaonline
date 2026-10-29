"""Drop from the rolling close high; static formula verified from 89868."""
def signal(df,n,col):
    df[col]=1-df['close']/(df['close'].rolling(int(n),min_periods=1).max()+1e-12)
    return df
def signal_multi_params(df,params):
    return {str(n):1-df['close']/(df['close'].rolling(int(n),min_periods=1).max()+1e-12) for n in params}
