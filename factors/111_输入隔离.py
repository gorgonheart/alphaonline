"""Research control: preserve 111 mathematics, isolate all input mutations."""
import importlib

_legacy = importlib.import_module('factors.111')


def signal_multi_params(df, param_list):
    return _legacy.signal_multi_params(df.copy(deep=True), param_list)
