"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
from core.okx.base_client import OkxClient


class OkxPortfolioMarginClient(OkxClient):
    """
    OKX 统一交易账户客户端。

    OKX 的现货与U本位永续本来就共享同一个交易账户（跨币种保证金模式），
    对应 Binance 的统一账户形态，基类 OkxClient 已按此实现，
    这里保留子类仅为与 core/binance 的目录结构（base + 账户类型子类）保持一致，
    后续如需支持 OKX 其他账户模式（如组合保证金 acctLv=4 的差异化处理）可在此扩展。
    """
    pass
