"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
# ====================================================================================================
# ** 交易所分发中心 **
# 框架默认对接 Binance，账户配置中通过 account_config['exchange_name'] = 'okx' 切换到 OKX。
# 每个实盘进程只服务一个账户（X3S_TRADING_ACCOUNT），因此交易所选择是进程级别的，
# 各处需要「无账户上下文」使用交易所能力的地方（数据过滤、时差刷新、拆单精度等），
# 统一从这里获取对应交易所的客户端类 / dummy client / 市场信息缓存。
#󠀂󠁥󠀹󠁢󠁥󠀹󠁡󠁥󠀵󠀸󠁤󠀹󠁡󠁥󠀸󠀸󠀷󠁢󠀴󠀲󠀰󠀳󠀳󠀳󠀲󠀳󠀲󠀳󠀹󠀳󠀷󠁿
# symbol 约定：
# - binance: BTCUSDT（现货与合约同名，靠 symbol_type 区分）
# - okx:     原生 instId，现货 BTC-USDT，永续 BTC-USDT-SWAP（与 OKX 数据中心的预处理数据一致）
# ====================================================================================================

from core.utils.log_kit import logger

EXCHANGE_BINANCE = 'binance'
EXCHANGE_OKX = 'okx'


def get_exchange_name() -> str:
    import config
    return getattr(config, 'exchange_name', EXCHANGE_BINANCE)


def is_okx() -> bool:
    return get_exchange_name() == EXCHANGE_OKX


def get_base_client_class():
    """获取当前交易所的客户端基类"""
    if is_okx():
        from core.okx.base_client import OkxClient
        return OkxClient
    from core.binance.base_client import BinanceClient
    return BinanceClient


def get_dummy_client():
    """获取当前交易所的无鉴权客户端，用于公共行情/市场信息类请求"""
    return get_base_client_class().get_dummy_client()


def get_market_info_store() -> dict:
    """
    获取当前交易所客户端的市场信息缓存（类级共享字典）。
    结构: {'spot': {...}, 'swap': {...}}，与 BinanceClient.market_info 相同
    """
    return get_base_client_class().market_info


def get_bstocks_coins() -> set:
    """
    获取当前交易所的 bStocks（股票代币）基础币代码集合，用于选币/交易过滤。
    多态实现（与 get_market_info_store 同一模式）：binance 返回 BinanceClient 的类级全局缓存，
    OKX 没有 bStocks，OkxClient.bstocks_coins 恒为空集
    """
    return get_base_client_class().bstocks_coins


def apply_bstocks_black_list(conf) -> list:
    """
    把当前 bStocks（股票代币）名单显式并入 conf.black_list。

    :param conf: 含 black_list 属性的配置对象（如 BacktestConfig / MultiEquityBacktestConfig）
    :return: 本次新并入黑名单的 bStocks 现货交易对列表（已排序）
    """
    coins = get_bstocks_coins()
    if not coins:
        logger.debug('bStocks 名单为空（无 apikey / 未刷新 / OKX），跳过黑名单合并')
        return []

    bstocks_symbols = {f'{coin}USDT' for coin in coins}
    added = sorted(bstocks_symbols - set(conf.black_list))
    conf.black_list = list(conf.black_list) + added

    if added:
        logger.info(f'🚫 bStocks 动态过滤：{len(added)} 个股票代币并入黑名单'
                    f'（来源: BN 钱包配置 / 代币转换资产接口，缓存共 {len(coins)} 个）: {added}')
    else:
        logger.info(f'🚫 bStocks 动态过滤：{len(coins)} 个股票代币均已在黑名单中，无需并入')

    return added


def okx_is_trade_symbol(symbol, black_list, white_list) -> bool:
    """
    OKX 版本的可交易判断：使用原生 instId（BTC-USDT / BTC-USDT-SWAP），
    只交易 USDT 交易对，剔除稳定币与黑名单
    """
    import config

    symbol = str(symbol).upper() if symbol else ''
    if white_list:
        return symbol in white_list
    if not symbol or not symbol.endswith(('-USDT', '-USDT-SWAP')) or symbol in black_list:
        return False
    return symbol.split('-')[0] not in config.stable_symbol


def normalize_symbol_filter_list(items) -> list:
    """
    黑名单/白名单归一化：
    - binance: 配置形如 LUNA-USDT，去掉 '-' 得到实盘的 LUNAUSDT
    - okx:     统一展开成现货 + 永续两个 instId（LUNA-USDT / LUNA-USDT-SWAP），
               确保和数据、行情中使用的原生 instId 能匹配上
    """
    items = items or []
    if not is_okx():
        return [item.replace('-', '') for item in items]

    result = []
    for item in items:
        item = str(item).upper()
        if item.endswith('-USDT-SWAP'):
            base = item[:-len('-USDT-SWAP')]
        elif item.endswith('-USDT'):
            base = item[:-len('-USDT')]
        elif item.endswith('USDT'):
            base = item[:-len('USDT')]
        else:
            result.append(item)
            continue
        result.extend([f'{base}-USDT', f'{base}-USDT-SWAP'])
    # 保持顺序去重
    return list(dict.fromkeys(result))
