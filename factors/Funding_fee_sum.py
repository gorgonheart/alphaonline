import pandas as pd


def signal(*args):
    df = args[0]
    n = int(args[1])
    factor_name = args[2]

    funding = pd.to_numeric(df['funding_fee'], errors='coerce')
    df[factor_name] = funding.rolling(n).sum()
    return df


def signal_multi_params(df, param_list):
    """Calculate rolling funding-fee sums with full-window semantics."""
    funding = pd.to_numeric(df['funding_fee'], errors='coerce')
    return {
        str(param): funding.rolling(int(param)).sum()
        for param in param_list
    }
