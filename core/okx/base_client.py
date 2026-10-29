"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
# ==================================================================================================
# !!! 前置非常重要说明
# ---------------------------------------------------------------------------------------------------
# ** 账户说明 **
# OKX 统一交易账户（跨币种保证金模式）：现货与U本位永续在同一个交易账户内，共享保证金，无需划转。
# 结构上对应 Binance 的统一账户（PortfolioMarginClient）。
#󠀂󠁥󠀹󠁢󠁥󠀹󠁡󠁥󠀵󠀸󠁤󠀹󠁡󠁥󠀸󠀸󠀷󠁢󠀴󠀲󠀰󠀳󠀳󠀳󠀲󠀳󠀲󠀳󠀹󠀳󠀷󠁿
# ** symbol 约定 **
# OKX 数据中心的预处理数据使用原生 instId，本框架对 OKX 账户全程沿用 instId：
#   - 现货: BTC-USDT
#   - 永续: BTC-USDT-SWAP
#󠀂󠁥󠀹󠁢󠁥󠀹󠁡󠁥󠀵󠀸󠁤󠀹󠁡󠁥󠀸󠀸󠀷󠁢󠀴󠀲󠀰󠀳󠀳󠀳󠀲󠀳󠀲󠀳󠀹󠀳󠀷󠁿
# ** 数量约定 **
# OKX 永续下单/持仓的原生单位是「张」，本框架内部一律使用「币数量」，
# 仅在与交易所交互的边界（下单参数、回执解析、持仓解析）做币数 <-> 张数换算：
#   币数量 = 张数 × ctVal × ctMult
#󠀂󠁥󠀹󠁢󠁥󠀹󠁡󠁥󠀵󠀸󠁤󠀹󠁡󠁥󠀸󠀸󠀷󠁢󠀴󠀲󠀰󠀳󠀳󠀳󠀲󠀳󠀲󠀳󠀹󠀳󠀷󠁿
# ** 回执约定 **
# OKX 下单接口的即时响应不包含成交信息（只有 ordId/sCode），本客户端在下单后主动查单，
# 并把回执归一化成 Binance 形状（status/origQty/executedQty/avgPrice/cumQuote/updateTime...），
# 让下游的成交额统计（build_order_records）与 IOC 部分成交重试逻辑零改动复用。
#󠀂󠁥󠀹󠁢󠁥󠀹󠁡󠁥󠀵󠀸󠁤󠀹󠁡󠁥󠀸󠀸󠀷󠁢󠀴󠀲󠀰󠀳󠀳󠀳󠀲󠀳󠀲󠀳󠀹󠀳󠀷󠁿
# ** 方法名前缀规范（与 BinanceClient 一致）**
# 1. load_* 从硬盘获取数据
# 2. fetch_* 从接口获取数据
# 3. get_* 从对象获取数据，可能从硬盘，也可能从接口
# ====================================================================================================

import math
import os
import time
import traceback
import uuid

import ccxt
import numpy as np
import pandas as pd

from config import proxy, stable_symbol, error_webhook_url
from core.utils.commons import retry_wrapper, apply_precision
from core.utils.dingding import send_wechat_work_msg, send_msg_for_order
from core.utils.encryptor import AESEncryptor
from core.utils.log_kit import logger, divider


def _safe_float(data: dict, key: str, default: float = 0.0) -> float:
    """OKX 接口返回的数值可能是空字符串/None，统一做容错转换"""
    raw_value = data.get(key, '')
    if raw_value == '' or raw_value is None:
        return default
    try:
        return float(raw_value)
    except (ValueError, TypeError):
        return default


def okx_symbol_base(inst_id: str) -> str:
    """从 instId 中提取基础币种。例：BTC-USDT-SWAP -> BTC"""
    return inst_id.split('-')[0] if inst_id else ''


class OkxClient:
    diff_timestamp = 0  # 与交易所的时差。OKX 签名由 ccxt 自行处理，保留该属性仅为接口兼容
    constants = dict(
        spot_td_mode='cross',  # 现货下单的 tdMode。跨币种保证金模式下 cross 支持自动借币，对齐 BN 统一账户的杠杆现货
        swap_td_mode='cross',  # 合约下单的 tdMode
    )

    market_info = {}  # 缓存市场信息，并且自动更新，全局共享（与 BinanceClient 保持类级共享一致）

    bstocks_coins: set = set()  # OKX 没有 bStocks（股票代币），恒为空集，仅为与 BinanceClient 统一客户端接口

    # OKX broker 标记，下单时随 tag 上报
    BROKER_TAG = '91b45db660c6BCDE'

    def __init__(self, **config):
        self.api_key: str = config.get('apiKey', '')
        self.secret: str = config.get('secret', '')
        self.password: str = config.get('password', '')  # OKX 特有的 passphrase
        self.use_private_instruments: bool = config.get(
            'use_private_instruments', bool(self.api_key)
        )

        self.order_money_limit: dict = {
            'spot': config.get('spot_order_money_limit', 10),
            'swap': config.get('swap_order_money_limit', 5),
        }

        exchange_config = {
            'apiKey': self.api_key,
            'secret': self.secret,
            'password': self.password,
            'timeout': 30000,
            'rateLimit': 30,
            'enableRateLimit': False,
            'options': {
                'defaultType': 'swap',
            },
            'proxies': proxy,
        }
        self.exchange = ccxt.okx(self.decrypt(exchange_config))

        # 模拟盘配置（OKX demo trading）
        self.demo: bool = config.get('demo', False)
        if self.demo:
            self.exchange.set_sandbox_mode(True)

        self.wechat_webhook_url: str = config.get('wechat_webhook_url', '')

        self.swap_account = None

        self.coin_margin: dict = config.get('coin_margin', {})  # 用做保证金的币种（OKX 暂不支持该功能）
        if self.coin_margin:
            logger.warning('OKX 客户端暂不支持 coin_margin 联合保证金功能，该配置将被忽略')

        self.base_margin: dict = config.get('base_margin', {'USDT': 1})  # 额外基础保证金
        self.base_coin_amount: dict = {base_coin: 0 for base_coin in self.base_margin.keys()}

        self.seed_coins: list = config.get('seed_coins', [])  # 套利的底仓币种

        self._swap_ticker_price = {}
        self._spot_ticker_price = {}

        self._position_tiers_cache = {}  # {instId: tiers list}，OKX 的杠杆分层数据按 instId 懒加载

    @property
    def base_coins(self):
        return list(self.base_margin.keys())

    @staticmethod
    def decrypt(exchange_config):
        # 从环境变量中获取密钥
        x3s_trading_secret_key = os.getenv('X3S_TRADING_SECRET_KEY')

        if x3s_trading_secret_key:
            encryptor = AESEncryptor(x3s_trading_secret_key)
            if 'apiKey' in exchange_config and 'secret' in exchange_config and not exchange_config.get('decrypt', False):
                try:
                    if exchange_config['apiKey']:
                        exchange_config['apiKey'] = encryptor.decrypt(exchange_config['apiKey'])
                    if exchange_config['secret']:
                        exchange_config['secret'] = encryptor.decrypt(exchange_config['secret'])
                    if exchange_config.get('password'):
                        exchange_config['password'] = encryptor.decrypt(exchange_config['password'])
                    exchange_config['decrypt'] = True
                except Exception as e:
                    logger.error(e)
                    logger.error(traceback.format_exc())
                    logger.error(f'解密 OKX apiKey、secret 和 password 发生错误，请检查配置是否正确，程序自动退出。')
                    send_wechat_work_msg('解密 OKX apiKey、secret 和 password 发生错误，请检查配置是否正确', error_webhook_url)
                    exit(1)

        return exchange_config

    # ====================================================================================================
    # ** 市场信息 **
    # ====================================================================================================
    def _fetch_instruments(self, inst_type: str) -> list:
        """
        获取交易产品基础信息。
        正式模式且配置 API key 时使用私有接口，获取账户实际可交易列表；
        debug/simulate 模式或 dummy client 使用公共接口，避免占位凭证触发认证。
        """
        if self.api_key and self.use_private_instruments:
            api_func = self.exchange.private_get_account_instruments
        else:
            api_func = self.exchange.public_get_public_instruments
        instruments = retry_wrapper(api_func, params={'instType': inst_type},
                                    func_name=f'获取OKX {inst_type} 币种规则数据')
        return instruments.get('data', []) if instruments else []

    # region 市场信息数据获取
    def fetch_market_info(self, symbol_type='swap', quote_symbol='USDT'):
        """
        加载市场数据
        :param symbol_type: 币种信息。swap为合约，spot为现货
        :param quote_symbol: 报价币种
        :return:
            symbol_list     交易对列表（instId 格式）
            price_precision 币种价格精度   例：2 代表 0.01
            min_qty         下单数量精度（币数量的小数位数）。合约按 ctVal×ctMult×lotSz 折算成币数量精度
            min_notional    最小下单金额（USDT）
            另附 OKX 特有字段：ct_val / ct_mult / lot_sz / min_sz / max_leverage
        """
        logger.debug(f'🔢 更新OKX {symbol_type}市场数据...')
        if symbol_type == 'swap':
            inst_type = 'SWAP'
            suffix = f'-{quote_symbol}-SWAP'
        else:
            inst_type = 'SPOT'
            suffix = f'-{quote_symbol}'

        instrument_list = self._fetch_instruments(inst_type)

        symbol_list = []  # 可交易的 USDT 交易对（剔除稳定币）
        full_symbol_list = []  # 全量 USDT 交易对
        min_qty = {}  # 下单数量精度（币数量的小数位数）
        price_precision = {}  # 价格精度
        min_notional = {}  # 最小下单金额（USDT）
        ct_val = {}  # 合约面值（仅 swap）
        ct_mult = {}  # 合约乘数（仅 swap）
        lot_sz = {}  # 下单数量步长（原生单位：swap 为张，spot 为币）
        min_sz = {}  # 最小下单数量（原生单位：swap 为张，spot 为币）
        max_leverage = {}  # 页面最大杠杆（仅 swap）
        inst_family = {}  # instId -> 交易所返回的合约家族（仅 swap）

        for info in instrument_list:
            inst_id = info.get('instId', '')

            # 过滤掉非报价币对
            if not inst_id.endswith(suffix):
                continue

            # 过滤非可交易状态的币种
            if info.get('state', '') != 'live':
                continue

            # 过滤盘前交易币种
            if info.get('ruleType', '') == 'pre_market':
                continue

            full_symbol_list.append(inst_id)

            # 稳定币不参与交易
            if okx_symbol_base(inst_id) in stable_symbol:
                pass
            else:
                symbol_list.append(inst_id)

            # ===价格精度
            tick_size = _safe_float(info, 'tickSz', 0.0)
            if tick_size > 0:
                precision = int(round(math.log(tick_size, 0.1)))
                if not math.isclose(tick_size, 10 ** (-precision), rel_tol=1e-6):
                    # 非 10 的幂的 tickSz（如 0.5 / 2.5）无法用小数位数精确表达，
                    # 向更粗的精度取整，保证取整后的价格始终是 tickSz 的整数倍
                    precision = int(math.floor(math.log(tick_size, 0.1)))
                    logger.warning(f'[{inst_id}] tickSz {tick_size} 不是10的幂，价格精度按 {precision} 位处理')
                price_precision[inst_id] = precision
            else:
                price_precision[inst_id] = 2

            # ===下单数量精度（统一折算成币数量的小数位数）
            lot_size = _safe_float(info, 'lotSz', 0.0)
            lot_sz[inst_id] = lot_size
            min_sz[inst_id] = _safe_float(info, 'minSz', 1.0)

            if symbol_type == 'swap':
                ct_val[inst_id] = _safe_float(info, 'ctVal', 1.0)
                ct_mult[inst_id] = _safe_float(info, 'ctMult', 1.0)
                max_leverage[inst_id] = int(_safe_float(info, 'lever', 5.0))
                family = info.get('instFamily', '')
                if family:
                    inst_family[inst_id] = family
                # 一张合约对应的币数量
                coin_lot = ct_val[inst_id] * ct_mult[inst_id] * (lot_size if lot_size > 0 else 1.0)
            else:
                coin_lot = lot_size if lot_size > 0 else 0.001

            precision = int(round(math.log(coin_lot, 0.1)))
            if not math.isclose(coin_lot, 10 ** (-precision), rel_tol=1e-6):
                # 非 10 的幂的下单步长无法用小数位数精确表达，向更粗的精度取整保证数量始终是步长的整数倍
                precision = int(math.floor(math.log(coin_lot, 0.1)))
                logger.warning(f'[{inst_id}] 下单步长 {coin_lot} 不是10的幂，数量精度按 {precision} 位处理')
            min_qty[inst_id] = precision

        # ===计算最小下单金额（OKX 没有 minNotional 字段，需要用最小下单数量 × 最新价格折算）
        ticker_list = self.fetch_ticker_price(symbol_type=symbol_type)
        price_dict = {t['symbol']: float(t['price']) for t in ticker_list}
        for inst_id in full_symbol_list:
            price = price_dict.get(inst_id, 0.0)
            if symbol_type == 'swap':
                min_notional[inst_id] = min_sz.get(inst_id, 1.0) * ct_val.get(inst_id, 1.0) * ct_mult.get(inst_id, 1.0) * price
            else:
                min_notional[inst_id] = min_sz.get(inst_id, 1.0) * price

        self.market_info[symbol_type] = {
            'symbol_list': symbol_list,
            'full_symbol_list': full_symbol_list,
            'min_qty': min_qty,
            'price_precision': price_precision,
            'min_notional': min_notional,
            'ct_val': ct_val,
            'ct_mult': ct_mult,
            'lot_sz': lot_sz,
            'min_sz': min_sz,
            'max_leverage': max_leverage,
            'inst_family': inst_family,
            'last_update': int(time.time())
        }
        return self.market_info[symbol_type]

    def get_market_info(self, symbol_type, expire_seconds: int = 3600 * 12, require_update: bool = False,
                        quote_symbol='USDT') -> dict:
        if require_update:  # 如果强制刷新的话，就当我们系统没有更新过
            last_update = 0
        else:
            last_update = self.market_info.get(symbol_type, {}).get('last_update', 0)
        if last_update + expire_seconds < int(time.time()):
            self.fetch_market_info(symbol_type, quote_symbol)

        return self.market_info[symbol_type]

    # endregion

    # ====================================================================================================
    # ** 下单参数辅助 **
    # ====================================================================================================
    @staticmethod
    def _new_client_order_id() -> str:
        """
        生成客户端订单ID。OKX 要求 clOrdId 在活跃订单中唯一，
        纯时间戳在批量下单/同单重试时会撞号被拒（51016），追加随机后缀保证唯一
        """
        return f'x{int(time.time() * 1000)}{uuid.uuid4().hex[:6]}'

    @staticmethod
    def _format_price(price: float, precision: int) -> str:
        """按精度取整并格式化价格。精度可能为负（tickSz>=10 或非10幂 tickSz 向粗取整）"""
        return f'{round(float(price), precision):.{max(precision, 0)}f}'

    # ====================================================================================================
    # ** 币数量 <-> 合约张数换算 **
    # ====================================================================================================
    def _coin_size_per_contract(self, inst_id: str) -> float:
        """一张合约对应的币数量 = ctVal × ctMult"""
        market_info = self.get_market_info('swap')
        return market_info.get('ct_val', {}).get(inst_id, 1.0) * market_info.get('ct_mult', {}).get(inst_id, 1.0)

    def coins_to_contracts(self, inst_id: str, coins: float) -> float:
        """币数量 -> 合约张数，并按张数步长（lotSz）取整"""
        market_info = self.get_market_info('swap')
        contracts = coins / self._coin_size_per_contract(inst_id)
        lot = market_info.get('lot_sz', {}).get(inst_id, 1.0) or 1.0
        # 取整到 lotSz 的整数倍，避免出现非法张数
        contracts = round(contracts / lot) * lot
        # 消除浮点误差，例如 0.30000000000000004
        return float(np.format_float_positional(contracts, precision=12, fractional=True, trim='-'))

    def contracts_to_coins(self, inst_id: str, contracts: float) -> float:
        """合约张数 -> 币数量"""
        return contracts * self._coin_size_per_contract(inst_id)

    # ====================================================================================================
    # ** 行情数据获取 **
    # ====================================================================================================
    # region 行情数据获取
    """K线数据获取"""

    def get_candle_df(self, symbol, run_time, limit=1500, interval='1h', symbol_type='swap') -> pd.DataFrame:
        """
        获取K线数据，返回与 BinanceClient.get_candle_df 相同结构的 DataFrame。
        OKX K线接口单次最多返回 100 根（history-candles），且按时间倒序，需要翻页并反转。
        """
        bar_map = {'1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m',
                   '1h': '1H', '2h': '2H', '4h': '4H', '6h': '6H', '12h': '12H', '1d': '1Dutc'}
        bar = bar_map.get(interval, interval)

        start_time_dt = run_time - pd.to_timedelta(interval) * limit
        start_ts = int(time.mktime(start_time_dt.timetuple())) * 1000

        raw_list = []
        after = ''  # 请求早于该时间戳的数据（OKX 语义），从最新往回翻页
        while True:
            params = {'instId': symbol, 'bar': bar, 'limit': '100'}
            if after:
                params['after'] = after
            try:
                response = retry_wrapper(
                    self.exchange.public_get_market_history_candles, params=params,
                    func_name='获取OKX币种K线', if_exit=False
                )
            except Exception as e:
                logger.error(e)
                logger.error(traceback.format_exc())
                return pd.DataFrame()

            data = (response or {}).get('data', [])
            if not data:
                break
            raw_list.extend(data)

            oldest_ts = int(data[-1][0])
            if len(raw_list) >= limit or oldest_ts <= start_ts:
                break
            after = str(oldest_ts)
            time.sleep(0.1)

        if not raw_list:
            return pd.DataFrame()

        # OKX 返回: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]，按时间倒序
        df = pd.DataFrame(raw_list, columns=['candle_begin_time', 'open', 'high', 'low', 'close',
                                             'volume', 'volume_ccy', 'quote_volume', 'confirm'])
        for col in ('open', 'high', 'low', 'close', 'volume', 'quote_volume'):
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df['candle_begin_time'] = pd.to_datetime(pd.to_numeric(df['candle_begin_time']), unit='ms')
        df.drop(columns=['volume_ccy', 'confirm'], inplace=True)

        # swap 的 vol 单位是张，折算成币数量与 Binance 对齐
        if symbol_type == 'swap':
            df['volume'] = df['volume'] * self._coin_size_per_contract(symbol)

        df['symbol'] = symbol
        df['symbol_type'] = symbol_type
        df.sort_values(by=['candle_begin_time'], inplace=True)
        df.drop_duplicates(subset=['candle_begin_time'], keep='last', inplace=True)

        # 只保留 start_time 之后的数据，并删除 run_time 未走完的K线
        from config import utc_offset
        df = df[df['candle_begin_time'] >= pd.to_datetime(start_ts, unit='ms')]
        df = df[df['candle_begin_time'] + pd.Timedelta(hours=utc_offset) < run_time]
        df.reset_index(drop=True, inplace=True)

        return df

    def get_candle_df_bulk(self, symbol_list, run_time, limit, interval='1h', symbol_type='swap') -> dict:
        result = {}
        for symbol in symbol_list:
            df = self.get_candle_df(symbol, run_time, limit, interval=interval, symbol_type=symbol_type)
            if df is None or df.empty:
                continue
            result[symbol] = df
        return result

    """最新报价数据获取"""

    def fetch_ticker_price(self, symbol: str = None, symbol_type: str = 'swap'):
        """
        获取最新价格。与 BinanceClient 一致：
        - 传 symbol 返回单个 {'symbol': ..., 'price': ...}
        - 不传返回全量 list[dict]
        """
        match symbol_type:
            case 'spot':
                inst_type, suffix = 'SPOT', '-USDT'
            case 'swap':
                inst_type, suffix = 'SWAP', '-USDT-SWAP'
            case _:
                raise ValueError(f'未知的symbol_type：{symbol_type}')

        if symbol:
            response = retry_wrapper(
                self.exchange.public_get_market_ticker, params={'instId': symbol},
                func_name=f'获取{symbol}的ticker数据'
            )
            data = (response or {}).get('data', [])
            if not data:
                return {'symbol': symbol, 'price': '0'}
            return {'symbol': symbol, 'price': str(_safe_float(data[0], 'last', 0.0))}

        response = retry_wrapper(
            self.exchange.public_get_market_tickers, params={'instType': inst_type},
            func_name=f'获取所有{symbol_type}币种的ticker数据'
        )
        result = []
        for ticker in (response or {}).get('data', []):
            inst_id = ticker.get('instId', '')
            if not inst_id.endswith(suffix):
                continue
            result.append({'symbol': inst_id, 'price': str(_safe_float(ticker, 'last', 0.0))})
        return result

    def fetch_spot_ticker_price(self, spot_symbol: str = None):
        return self.fetch_ticker_price(spot_symbol, symbol_type='spot')

    def fetch_swap_ticker_price(self, swap_symbol: str = None):
        return self.fetch_ticker_price(swap_symbol, symbol_type='swap')

    def get_spot_ticker_price_series(self) -> pd.Series:
        ticker_price_df = pd.DataFrame(self.fetch_ticker_price(symbol_type='spot'))
        ticker_price_df['price'] = pd.to_numeric(ticker_price_df['price'], errors='coerce')
        self._spot_ticker_price = ticker_price_df.set_index(['symbol'])['price']
        return self._spot_ticker_price

    def get_swap_ticker_price_series(self) -> pd.Series:
        ticker_price_df = pd.DataFrame(self.fetch_ticker_price(symbol_type='swap'))
        ticker_price_df['price'] = pd.to_numeric(ticker_price_df['price'], errors='coerce')
        self._swap_ticker_price = ticker_price_df.set_index(['symbol'])['price']
        return self._swap_ticker_price

    """盘口数据获取"""

    def fetch_book_ticker(self, symbol, symbol_type='swap') -> dict:
        response = retry_wrapper(
            self.exchange.public_get_market_books, params={'instId': symbol, 'sz': '1'},
            func_name=f'获取{"合约" if symbol_type == "swap" else "现货"}盘口数据'
        )
        data = (response or {}).get('data', [])
        if not data:
            return {'symbol': symbol, 'bidPrice': '0', 'askPrice': '0'}
        bids = data[0].get('bids', [])
        asks = data[0].get('asks', [])
        return {
            'symbol': symbol,
            'bidPrice': bids[0][0] if bids else '0',
            'askPrice': asks[0][0] if asks else '0',
        }

    def fetch_spot_book_ticker(self, spot_symbol) -> dict:
        return self.fetch_book_ticker(spot_symbol, symbol_type='spot')

    def fetch_swap_book_ticker(self, swap_symbol) -> dict:
        return self.fetch_book_ticker(swap_symbol, symbol_type='swap')

    # endregion

    # ====================================================================================================
    # ** 资金费数据 **
    # ====================================================================================================
    def get_funding_rate_df(self, symbol, limit=100) -> pd.DataFrame:
        """
        获取OKX的历史资金费数据（单币种）。OKX 单次最多返回 100 条。
        """
        params = {'instId': symbol, 'limit': str(min(limit, 100))}
        try:
            response = retry_wrapper(
                self.exchange.public_get_public_funding_rate_history, params=params,
                func_name='获取合约历史资金费数据'
            )
        except Exception as e:
            logger.debug(e)
            return pd.DataFrame()

        data = (response or {}).get('data', [])
        if not data:
            return pd.DataFrame()

        funding_df = pd.DataFrame(data)
        funding_df.rename(columns={'instId': 'symbol'}, inplace=True)
        funding_df['fundingTime'] = pd.to_datetime(pd.to_numeric(funding_df['fundingTime']), unit='ms')
        funding_df['fundingRate'] = pd.to_numeric(funding_df['fundingRate'], errors='coerce')
        funding_df = funding_df[['symbol', 'fundingTime', 'fundingRate']]
        funding_df.sort_values('fundingTime', inplace=True)
        return funding_df

    def get_premium_index_df(self) -> pd.DataFrame:
        """OKX 没有全市场资金费的批量接口，如需请按币种调用 get_funding_rate_df"""
        raise NotImplementedError('OKX 暂不支持批量获取全市场资金费，请按币种调用 get_funding_rate_df')

    # ====================================================================================================
    # ** 账户设置 **
    # ====================================================================================================
    def set_single_side_position(self):
        """检查并设置单向持仓（买卖模式 net_mode）"""
        response = retry_wrapper(
            self.exchange.private_get_account_config, func_name='查询OKX账户配置', if_exit=False
        )
        data = (response or {}).get('data', [])
        if not data:
            logger.warning('无法获取OKX账户配置，跳过持仓模式检查')
            return

        if data[0].get('posMode', '') == 'net_mode':
            return

        retry_wrapper(
            self.exchange.private_post_account_set_position_mode, params={'posMode': 'net_mode'},
            func_name='设置OKX单向持仓', if_exit=False
        )
        logger.info('修改OKX持仓模式为单向持仓（买卖模式）')

    def set_dual_side_position(self):
        retry_wrapper(
            self.exchange.private_post_account_set_position_mode, params={'posMode': 'long_short_mode'},
            func_name='设置OKX双向持仓', if_exit=False
        )
        logger.info('修改OKX持仓模式为双向持仓（开平仓模式）')

    def set_multi_assets_margin(self):
        """
        检查账户模式。OKX 需要账户处于跨币种保证金模式（acctLv=3）或组合保证金模式（acctLv=4），
        才能实现现货+合约共享保证金（对应 Binance 的联合保证金/统一账户）。
        """
        response = retry_wrapper(
            self.exchange.private_get_account_config, func_name='获取OKX账户模式', if_exit=False
        )
        data = (response or {}).get('data', [])
        if not data:
            logger.warning('无法获取OKX账户配置，跳过账户模式检查')
            return

        acct_lv = data[0].get('acctLv', '')
        if acct_lv in ('3', '4'):
            return

        retry_wrapper(
            self.exchange.private_post_account_set_account_level, params={'acctLv': '3'},
            func_name='设置OKX账户为跨币种保证金模式', if_exit=False
        )
        logger.ok('开启OKX跨币种保证金模式')

    def _get_position_tiers(self, inst_id: str) -> list:
        """使用 instruments 返回的 instFamily 查询仓位档位，结果按完整 instId 懒加载缓存"""
        if inst_id not in self._position_tiers_cache:
            market_info = self.get_market_info('swap')
            inst_family = market_info.get('inst_family', {}).get(inst_id, '')
            if not inst_family:
                logger.warning(f'[{inst_id}] instruments 数据缺少 instFamily，跳过仓位档位查询')
                return []
            response = retry_wrapper(
                self.exchange.public_get_public_position_tiers,
                params={'instType': 'SWAP', 'tdMode': 'cross', 'instId': inst_id, 'instFamily': inst_family},
                func_name=f'获取{inst_id}仓位档位', if_exit=False
            )
            self._position_tiers_cache[inst_id] = (response or {}).get('data', [])
        return self._position_tiers_cache[inst_id]

    def get_swap_leverage_bracket(self, force_refresh: bool = False):
        """OKX 的档位数据按 instId 懒加载，此处返回已缓存的数据，仅为接口兼容"""
        if force_refresh:
            self._position_tiers_cache = {}
        return self._position_tiers_cache

    def _pick_leverage_for_notional(self, inst_id: str, target_notional: float, price: float,
                                    buffer_ratio: float = 0.9, floor: int = 1):
        """
        根据目标名义价值挑选满足档位容量的最大杠杆。
        OKX 档位的容量单位是张数（maxSz），需要用价格折算成名义价值。
        :return: (picked_leverage, overflowed)
        """
        tiers = self._get_position_tiers(inst_id)
        if not tiers or price <= 0:
            return floor, False

        coin_per_contract = self._coin_size_per_contract(inst_id)
        candidates = []
        for tier in tiers:
            max_sz = _safe_float(tier, 'maxSz', 0.0)
            max_lever = int(_safe_float(tier, 'maxLever', 1.0))
            notional_cap = max_sz * coin_per_contract * price
            candidates.append((notional_cap, max_lever))

        candidates.sort(key=lambda item: item[0])
        for notional_cap, max_lever in candidates:
            if notional_cap * buffer_ratio >= target_notional:
                return max(floor, max_lever), False
        # 全部档位都不够 → 取容量最大的那一档
        return max(floor, candidates[-1][1]), True

    def _fetch_current_leverage(self, inst_ids: list) -> dict:
        """批量查询当前杠杆配置，OKX 单次最多支持 20 个 instId"""
        lever_dict = {}
        for i in range(0, len(inst_ids), 20):
            batch = inst_ids[i:i + 20]
            response = retry_wrapper(
                self.exchange.private_get_account_leverage_info,
                params={'instId': ','.join(batch), 'mgnMode': 'cross'},
                func_name='查询OKX当前杠杆', if_exit=False
            )
            for row in (response or {}).get('data', []):
                lever_dict[row.get('instId', '')] = int(_safe_float(row, 'lever', 0))
            time.sleep(0.1)
        return lever_dict

    def _set_leverage(self, inst_id: str, leverage: int):
        retry_wrapper(
            self.exchange.private_post_account_set_leverage,
            params={'instId': inst_id, 'lever': str(int(leverage)), 'mgnMode': 'cross'},
            func_name=f'设置{inst_id}杠杆', if_exit=False
        )

    def reset_max_leverage(self, max_leverage: int = 3, coin_list: tuple = (),
                           buffer_ratio: float = 0.9, floor: int = 1):
        """
        重置页面杠杆。
        与 Binance 不同：OKX 全量币种的杠杆查询/设置需要逐个 instId 调用，成本过高，
        这里只对「当前持仓币种 + 指定币种」做校准，新开仓币种由下单前的
        adjust_leverage_by_target_notional 兜底。
        """
        target_ids = set(coin_list)
        swap_position_df = self.get_swap_position_df()
        target_ids.update(swap_position_df.index.to_list())

        target_ids = sorted(target_ids)
        if not target_ids:
            logger.debug('OKX 当前无持仓且未指定币种，跳过页面杠杆重置')
            return

        market_info = self.get_market_info('swap')
        symbol_max_leverage = market_info.get('max_leverage', {})
        current_leverage = self._fetch_current_leverage(target_ids)

        for inst_id in target_ids:
            desired = min(max_leverage, symbol_max_leverage.get(inst_id, max_leverage))
            desired = max(floor, desired)
            if current_leverage.get(inst_id, 0) != desired:
                self._set_leverage(inst_id, desired)
                logger.info(f'[{inst_id}] 页面杠杆 {current_leverage.get(inst_id)}x → {desired}x')
                time.sleep(0.1)

    def adjust_leverage_by_target_notional(self, target_notional: dict, max_leverage: int = 3,
                                           buffer_ratio: float = 0.9, floor: int = 1):
        """
        根据目标持仓名义价值精调页面杠杆。在下单前调用，避免 OKX 档位容量不足导致拒单。
        :param target_notional: {instId: abs_notional} 字典，仅传 swap 行
        """
        if not target_notional:
            return

        inst_ids = [inst_id for inst_id, notional in target_notional.items() if notional and float(notional) > 0]
        if not inst_ids:
            return

        swap_price = self._swap_ticker_price if len(self._swap_ticker_price) else self.get_swap_ticker_price_series()
        current_leverage = self._fetch_current_leverage(inst_ids)

        for inst_id in inst_ids:
            notional = float(target_notional[inst_id])
            price = float(swap_price.get(inst_id, 0) or 0)
            needed_lev, overflowed = self._pick_leverage_for_notional(inst_id, notional, price, buffer_ratio, floor)
            new_lev = max(floor, min(max_leverage, needed_lev))

            if current_leverage.get(inst_id, max_leverage) > new_lev:
                self._set_leverage(inst_id, new_lev)
                logger.info(f'[{inst_id}] 目标 notional={notional:.2f}，页面杠杆 {current_leverage.get(inst_id)}x → {new_lev}x')
                if overflowed:
                    msg = f'[{inst_id}] 目标持仓名义价值 {notional:.2f} 超过该币所有档位上限，已降至 {new_lev}x，可能仍会被拒单'
                    logger.warning(msg)
                    send_wechat_work_msg(msg, self.wechat_webhook_url)
                time.sleep(0.1)

    def get_unimmr(self):
        """OKX 跨币种保证金模式下的保证金率（mgnRatio），与 BN 的 uniMMR 同语义：越大越安全"""
        response = retry_wrapper(
            self.exchange.private_get_account_balance, func_name='获取OKX账户保证金率', if_exit=False
        )
        data = (response or {}).get('data', [])
        if not data:
            return 999
        mgn_ratio = _safe_float(data[0], 'mgnRatio', 0.0)
        return mgn_ratio if mgn_ratio > 0 else 999

    # ====================================================================================================
    # ** 交易函数 **
    # ====================================================================================================
    def _fetch_pending_orders(self, inst_type: str) -> list:
        """分页获取所有挂单"""
        all_orders = []
        seen_order_ids = set()
        after_id = ''
        while True:
            params = {'instType': inst_type, 'limit': '100'}
            if after_id:
                params['after'] = after_id
            response = retry_wrapper(
                self.exchange.private_get_trade_orders_pending, params=params,
                func_name=f'查询OKX {inst_type} 挂单', if_exit=False
            )
            page_orders = (response or {}).get('data', [])
            if not page_orders:
                break
            for order in page_orders:
                ord_id = order.get('ordId')
                if ord_id and ord_id not in seen_order_ids:
                    seen_order_ids.add(ord_id)
                    all_orders.append(order)
            if len(page_orders) < 100:
                break
            after_id = page_orders[-1].get('ordId', '')
            if not after_id:
                break
            time.sleep(0.1)
        return all_orders

    def _cancel_orders(self, orders: list, symbol_list=None):
        """按 instId 过滤后批量撤单，每批最多 20 个"""
        if symbol_list:
            orders = [order for order in orders if order.get('instId') in symbol_list]
        if not orders:
            return

        logger.info(f'取消挂单: {sorted({order.get("instId") for order in orders})}')
        cancel_params = [{'instId': order['instId'], 'ordId': order['ordId']} for order in orders]
        for i in range(0, len(cancel_params), 20):
            batch = cancel_params[i:i + 20]
            response = retry_wrapper(
                self.exchange.private_post_trade_cancel_batch_orders, params=batch,
                func_name='批量取消OKX挂单', if_exit=False
            )
            for result in (response or {}).get('data', []):
                if result.get('sCode') != '0':
                    logger.warning(f"订单 {result.get('ordId')} 撤单失败: {result.get('sMsg')}")
            time.sleep(0.1)

    def cancel_all_spot_orders(self, symbol_list=None):
        self._cancel_orders(self._fetch_pending_orders('SPOT'), symbol_list)

    def cancel_all_swap_orders(self, symbol_list=None):
        self._cancel_orders(self._fetch_pending_orders('SWAP'), symbol_list)

    # ====================================================================================================
    # ** 回执处理 **
    # ====================================================================================================
    def _query_order_state(self, inst_id: str, ord_id: str) -> dict:
        """查询订单最终状态。IOC/市价单会立即终结，轮询几次等待终态"""
        data = {}
        for _ in range(5):
            response = retry_wrapper(
                self.exchange.private_get_trade_order, params={'instId': inst_id, 'ordId': ord_id},
                func_name=f'查询OKX订单状态 {inst_id}', if_exit=False
            )
            rows = (response or {}).get('data', [])
            if rows:
                data = rows[0]
                if data.get('state') in ('filled', 'canceled', 'mmp_canceled'):
                    break
            time.sleep(0.3)
        return data

    def _normalize_order_receipt(self, order_data: dict, symbol_type: str) -> dict:
        """
        将 OKX 订单数据归一化成 Binance 形状的回执，供下游（成交额统计、消息推送、
        IOC 部分成交重试）零改动复用。数量字段统一折算成币数量。
        """
        if not order_data:
            return {}

        inst_id = order_data.get('instId', '')
        sz = _safe_float(order_data, 'sz', 0.0)
        acc_fill_sz = _safe_float(order_data, 'accFillSz', 0.0)
        if symbol_type == 'swap':
            orig_qty = self.contracts_to_coins(inst_id, sz)
            executed_qty = self.contracts_to_coins(inst_id, acc_fill_sz)
        else:
            orig_qty = sz
            executed_qty = acc_fill_sz

        avg_price = _safe_float(order_data, 'avgPx', 0.0)
        cum_quote = executed_qty * avg_price

        state = order_data.get('state', '')
        if state == 'filled':
            status = 'FILLED'
        elif executed_qty > 0:
            # canceled/partially_filled 且有成交：IOC 部分成交后剩余被撤，按部分成交处理
            status = 'PARTIALLY_FILLED'
        elif state in ('canceled', 'mmp_canceled'):
            status = 'EXPIRED'
        else:
            status = 'NEW'

        update_time = order_data.get('uTime') or order_data.get('cTime') or str(int(time.time() * 1000))

        return {
            'symbol': inst_id,
            'side': order_data.get('side', '').upper(),
            'status': status,
            'origQty': str(orig_qty),
            'executedQty': str(executed_qty),
            'avgPrice': str(avg_price),
            'cumQuote': str(cum_quote),
            'cummulativeQuoteQty': str(cum_quote),
            'price': order_data.get('px', '') or str(avg_price),
            'updateTime': update_time,
            'transactTime': update_time,
            'orderId': order_data.get('ordId', ''),
            'clientOrderId': order_data.get('clOrdId', ''),
            'symbol_type': symbol_type,
        }

    @staticmethod
    def _merge_receipts(receipts: list) -> dict:
        """
        合并多次重试产生的回执（IOC 部分成交后会对剩余量重下）。
        Binance 客户端只返回最后一次回执，OKX 这里做了改进：累计所有成交量与成交额，
        避免部分成交的量被漏记到成交额统计中。
        """
        receipts = [r for r in receipts if r]
        if not receipts:
            return {}
        if len(receipts) == 1:
            return receipts[0]

        total_executed = sum(float(r.get('executedQty', 0) or 0) for r in receipts)
        total_quote = sum(float(r.get('cumQuote', 0) or 0) for r in receipts)
        orig_qty = float(receipts[0].get('origQty', 0) or 0)  # 第一笔的下单量即本次交易的总量
        avg_price = total_quote / total_executed if total_executed > 0 else 0.0

        merged = dict(receipts[-1])
        merged['origQty'] = str(orig_qty)
        merged['executedQty'] = str(total_executed)
        merged['cumQuote'] = str(total_quote)
        merged['cummulativeQuoteQty'] = str(total_quote)
        merged['avgPrice'] = str(avg_price)
        if total_executed >= orig_qty * 0.999999:
            merged['status'] = 'FILLED'
        elif total_executed > 0:
            merged['status'] = 'PARTIALLY_FILLED'
        return merged

    @staticmethod
    def _has_partial_fill(order_res):
        """检查是否存在部分成交"""
        return 'executedQty' in order_res and order_res['executedQty'] != order_res['origQty']

    def _should_record_order_error(self, order_res):
        """判断是否需要记录订单错误"""
        if not order_res:
            return True
        if 'msg' in order_res:
            return True
        if self._has_partial_fill(order_res):
            return True
        return False

    # ====================================================================================================
    # ** 下单重试 **
    # ====================================================================================================
    def _parse_okx_price_limit(self, error_msg: str, side: str):
        """
        从OKX价格限制报错中解析允许的边界，兼容51135/51136/51137和51006文案。
        买单取最高买价，卖单取最低卖价，重试时用它做基准。
        """
        import re
        if side == 'buy':
            match = re.search(r'highest price limit for the buy leg is ([\d.,]+)', error_msg)
            if not match:
                match = re.search(r'max buy price:\s*([\d.,]+)', error_msg)
        else:
            match = re.search(r'lowest price limit for the sell leg is ([\d.,]+)', error_msg)
            if not match:
                match = re.search(r'min sell price:\s*([\d.,]+)', error_msg)
        if match:
            try:
                return float(match.group(1).replace(',', '').rstrip('. '))
            except ValueError:
                return None
        return None

    def retry_order_param(self, params, symbol_type, slip_rate, attempt, price_limit=None):
        """
        重试前调整下单参数（刷新价格、重算滑点）。有价格限制时以限制价为基准。
        """
        market_info = self.market_info[symbol_type]
        price_precision = market_info['price_precision']

        inst_id = params['instId']
        side = params.get('side', 'buy')

        symbol_ticker_price = (
            self.get_swap_ticker_price_series() if symbol_type == 'swap' else self.get_spot_ticker_price_series()
        )
        if inst_id not in symbol_ticker_price:
            logger.warning(f'{inst_id} 价格不存在，无法调整下单参数')
            return params

        if price_limit:
            # 在限制价基础上留 1% 余地，避免再次超限
            price = price_limit * 0.99 if side == 'buy' else price_limit * 1.01
        else:
            if side == 'buy':
                price = symbol_ticker_price[inst_id] * (1 + slip_rate * attempt)
            else:
                price = symbol_ticker_price[inst_id] * (1 - slip_rate * attempt)

        precision = price_precision.get(inst_id, 8)
        params['px'] = self._format_price(price, precision)
        return params

    def retry_wrapper_for_order(self, func, params=None, func_name='', retry_times=5, symbol_type='swap',
                                slip_rate=0.015):
        """
        下单 + 查单 + 重试的封装。
        - 下单成功后查询订单终态，归一化成 Binance 形状的回执
        - IOC 未完全成交时，对剩余数量更新参数继续重试，最终合并所有回执返回
        - 识别 OKX 错误码，对可恢复错误（价格超限等）调价重试，对终结性错误提前退出
        """
        if params is None:
            params = {}

        receipts = []
        price_limit = None
        min_sz = self.market_info.get('swap', {}).get('min_sz', {}) if symbol_type == 'swap' else {}

        for attempt in range(retry_times):
            try:
                if attempt >= 1:
                    logger.warning(f"调参前：币种:{params['instId']},下单量:{params['sz']}，价格:{params.get('px')}")
                    params = self.retry_order_param(params, symbol_type, slip_rate, attempt, price_limit)
                    # OKX 强制 clOrdId 唯一，重试必须换新的，否则会被 51016 拒单
                    params['clOrdId'] = self._new_client_order_id()
                    logger.warning(f"调参后：币种:{params['instId']},下单量:{params['sz']}，价格:{params.get('px')}")

                response = func(params=params)
                data = (response or {}).get('data', [])
                if not data or data[0].get('sCode') != '0':
                    s_msg = data[0].get('sMsg', 'Unknown error') if data else str(response)
                    s_code = data[0].get('sCode', '') if data else ''
                    raise ValueError(f'OKX下单失败 sCode={s_code} {s_msg}')

                # ===下单成功，查询订单终态并归一化回执
                order_state = self._query_order_state(params['instId'], data[0].get('ordId', ''))
                receipt = self._normalize_order_receipt(order_state, symbol_type)
                receipts.append(receipt)

                if params.get('ordType') == 'ioc':
                    # 判断是否完全成交，未完全成交时更新剩余数量继续重试
                    executed = float(receipt.get('executedQty', 0) or 0)
                    orig = float(receipt.get('origQty', 0) or 0)
                    remaining = orig - executed
                    if remaining > 0:
                        if symbol_type == 'swap':
                            remaining_sz = self.coins_to_contracts(params['instId'], remaining)
                            if remaining_sz < min_sz.get(params['instId'], 1.0):
                                logger.warning(f"剩余数量 {remaining} 不足最小张数，停止重试")
                                break
                            params['sz'] = np.format_float_positional(remaining_sz).rstrip('.')
                        else:
                            params['sz'] = np.format_float_positional(remaining).rstrip('.')
                        logger.warning(f"下单量:{orig}, 成交量:{executed}，未完全成交，更新下单量，准备重试。")
                        continue
                return self._merge_receipts(receipts)
            except Exception as e:
                logger.error(f'{func_name} 报错')
                logger.error(e)
                logger.debug(params)
                msg = str(e).strip()
                if '51137' in msg or '51135' in msg or '51136' in msg or '51006' in msg:
                    # 价格超出限制，解析OKX返回的限价做基准后重试
                    if price_limit is None:
                        price_limit = self._parse_okx_price_limit(msg, params.get('side', 'buy'))
                        if price_limit:
                            logger.info(f'{func_name} 解析到OKX价格限制: {price_limit}')
                    time.sleep(1)
                elif '51008' in msg:
                    logger.warning(f'{func_name} 下单资金不足')
                    raise ValueError(func_name, '下单资金不足')
                elif '51119' in msg:
                    logger.warning(f'{func_name} 下单资金不足（借币额度不足）')
                    raise ValueError(func_name, '下单资金不足（借币额度不足）')
                elif '51169' in msg or '51023' in msg:
                    logger.warning(f'{func_name} ReduceOnly订单被拒绝, 仓位已经平完')
                    break
                elif '51121' in msg:
                    logger.error(f'{func_name} 下单张数不是步长的整数倍，跳过')
                    break
                elif '51020' in msg:
                    logger.warning(f'{func_name} 下单数量不满足最小下单量，跳过')
                    break
                elif '51000' in msg:
                    logger.error(f'{func_name} 请求参数错误，不进行重试')
                    break
                elif '51024' in msg:
                    logger.error(f'{func_name} 账户状态受限，无法交易，跳过')
                    break
                elif '51016' in msg:
                    logger.warning(f'{func_name} clOrdId 重复，刷新后重试')
                    params['clOrdId'] = self._new_client_order_id()
                elif '50113' in msg or '50111' in msg:
                    logger.error(f'{func_name} API配置错误，可能写错或未配置权限')
                    break
                elif '51001' in msg:
                    logger.error(f'{func_name} 没有交易对')
                    break
                elif '51155' in msg:
                    logger.error(f'{func_name} 币种因合规限制无法交易，直接跳过')
                    break
                elif '50011' in msg:
                    logger.warning(f'{func_name} 请求过于频繁，休息 1s 后再次尝试')
                    time.sleep(1)
                else:
                    logger.error(f'{e}，报错内容如下')
                    divider(f'ERR:{func_name}', sep='-')
                    logger.debug(traceback.format_exc())
                    divider(f'ERR:{func_name}', sep='-')
        else:
            if receipts:  # 有部分成交时不抛异常，返回已成交部分
                return self._merge_receipts(receipts)
            raise ValueError(func_name, '报错重试次数超过上限，程序退出。')

        return self._merge_receipts(receipts)

    # ====================================================================================================
    # ** 下单入口 **
    # ====================================================================================================
    def place_spot_order(self, symbol, side, quantity, price=None, **kwargs) -> dict:
        divider(f'`{symbol}`现货下单 {side} {quantity}', '.')

        market_info = self.get_market_info('spot')
        price_precision = market_info['price_precision'].get(symbol, 8)

        params = {
            'instId': symbol,
            'tdMode': self.constants.get('spot_td_mode', 'cross'),
            'side': side.lower(),
            'ordType': 'market',
            'sz': np.format_float_positional(float(quantity)).rstrip('.'),
            'clOrdId': self._new_client_order_id(),
            'tag': self.BROKER_TAG,
            **kwargs,
        }
        if price is not None:
            # 与 Binance 的 LIMIT+IOC 对齐，OKX 的 ordType=ioc 即「立即成交并取消剩余」的限价单
            params['ordType'] = 'ioc'
            params['px'] = self._format_price(price, price_precision)
        else:
            # OKX 现货市价买单的 sz 默认按计价货币（USDT金额）解释，
            # 框架的数量语义是币数量，必须显式指定按交易货币解释
            params['tgtCcy'] = 'base_ccy'

        try:
            logger.info(f'现货下单参数：{params}')
            order_res = self.retry_wrapper_for_order(
                self.exchange.private_post_trade_order,
                params=params,
                func_name='现货下单',
                symbol_type='spot',
            )
            logger.ok(f'现货下单完成，现货下单信息结果：{order_res}')
        except Exception as e:
            logger.error(f'现货下单出错：{e}')
            send_wechat_work_msg(
                f'现货 {symbol} 下单 {float(quantity) * float(price or 0)}U 出错，请查看程序日志',
                self.wechat_webhook_url
            )
            return {}
        send_msg_for_order([{'symbol': symbol, 'side': side, 'price': price, 'quantity': quantity}],
                           [order_res], self.wechat_webhook_url)
        return order_res

    def place_swap_order(self, symbol, side, quantity, price=None, reduce_only=False, **kwargs) -> dict:
        divider(f'`{symbol}`U本位合约下单 {side} {quantity}', '.')

        market_info = self.get_market_info('swap')
        price_precision = market_info['price_precision'].get(symbol, 8)

        # 币数量 -> 合约张数
        contracts = self.coins_to_contracts(symbol, abs(float(quantity)))
        if contracts <= 0:
            logger.warning(f'{symbol} 折算成张数后为0，跳过下单')
            return {}

        params = {
            'instId': symbol,
            'tdMode': self.constants.get('swap_td_mode', 'cross'),
            'side': side.lower(),
            'ordType': 'market',
            'sz': np.format_float_positional(contracts).rstrip('.'),
            'clOrdId': self._new_client_order_id(),
            'tag': self.BROKER_TAG,
            **kwargs,
        }
        if price is not None:
            params['ordType'] = 'ioc'
            params['px'] = self._format_price(price, price_precision)
        if reduce_only:
            params['reduceOnly'] = 'true'

        try:
            logger.info(f'U本位合约下单参数：{params}')
            order_res = self.retry_wrapper_for_order(
                self.exchange.private_post_trade_order,
                params=params,
                func_name='U本位合约下单',
                symbol_type='swap',
            )
            logger.ok(f'U本位合约下单完成，U本位合约下单信息结果：{order_res}')
        except Exception as e:
            logger.error(f'U本位合约下单出错：{e}')
            send_wechat_work_msg(
                f'U本位合约 {symbol} 下单 {float(quantity) * float(price or 0)}U 出错，请查看程序日志',
                self.wechat_webhook_url
            )
            return {}
        send_msg_for_order([{'symbol': symbol, 'side': side, 'price': price, 'quantity': quantity}],
                           [order_res], self.wechat_webhook_url)
        return order_res

    def prepare_order_params_and_place_order(
            self, orders_df: pd.DataFrame, symbol_type: str,
            slip_rate: float = 0.015, **kwargs):
        """
        根据策略产生的订单数据，构建每个币种的下单参数并逐笔下单。
        与 BinanceClient 相同的返回结构：(success_list, error_list)
        :param orders_df: 策略产生的订单信息
        :param symbol_type: 下单类型。spot/swap
        :param slip_rate: 滑点
        :return: (success_list, error_list)：成功（含部分成交）回执列表 + 失败/未成交订单行列表
        """
        orders_df.set_index('symbol', inplace=True)  # 重新设置index

        symbol_ticker_price = self._spot_ticker_price if symbol_type == 'spot' else self._swap_ticker_price
        market_info = self.get_market_info(symbol_type)
        min_qty = market_info['min_qty']
        price_precision = market_info['price_precision']
        min_notional = market_info['min_notional']
        min_sz = market_info.get('min_sz', {})

        order_error_list = []
        order_success_list = []
        for symbol, row in orders_df.iterrows():
            # ===若当前币种没有最小下单精度、或最小价格精度，报错
            if (symbol not in min_qty) or (symbol not in price_precision):
                logger.error(f'当前币种{symbol}没有最小下单精度、或最小价格精度，币种信息异常')
                continue

            # ===计算下单量、方向、价格
            amount = row['拆单金额']
            if amount > 0:
                side = 'BUY'
                price = symbol_ticker_price[symbol] * (1 + slip_rate)
            elif amount < 0:
                side = 'SELL'
                price = symbol_ticker_price[symbol] * (1 - slip_rate)
            else:
                logger.warning('下单金额为0，不进行下单')
                continue
            price = round(price, price_precision[symbol])

            quantity = row['实际下单量']
            # 按照最小下单量对合约进行四舍五入，对现货就低不就高处理
            quantity = round(quantity, min_qty[symbol]) if symbol_type == 'swap' else apply_precision(quantity,
                                                                                                      min_qty[symbol])
            quantity = abs(quantity)
            if quantity == 0:
                logger.warning('下单量为0，不进行下单')
                continue

            # ===判断是否是清仓/减仓交易（仅缩减或平掉已有同向仓位，可使用 reduceOnly）
            reduce_only = True if row['交易模式'] in ('清仓', '减仓') else False

            # ===OKX 合约的最小张数是硬性限制，不满足最小张数的单（包括减仓单）交易所会直接拒绝
            if symbol_type == 'swap':
                contracts = self.coins_to_contracts(symbol, quantity)
                if contracts < min_sz.get(symbol, 1.0):
                    logger.warning(f'{symbol} 折算张数 {contracts} 小于最小张数 {min_sz.get(symbol, 1.0)}，跳过该笔交易')
                    logger.info(f'下单量：{quantity},价格：{price}')
                    continue

            # ===判断交易金额是否小于最小下单金额，小于的跳过
            if (quantity * price < min_notional.get(symbol, self.order_money_limit[symbol_type]) or
                    quantity * price < self.order_money_limit[symbol_type]):
                if not reduce_only or symbol_type == 'spot':  # 清仓/减仓状态不跳过（现货除外）
                    logger.warning(f'{symbol}交易金额小于最小下单金额，跳过该笔交易')
                    logger.info(f'下单量：{quantity},价格：{price}')
                    continue

            # ===直接下单
            if symbol_type == 'spot':
                order_res = self.place_spot_order(symbol, side, quantity, price, **kwargs)
            else:
                order_res = self.place_swap_order(symbol, side, quantity, price, reduce_only=reduce_only, **kwargs)

            # 返回值为空，下单报错，存在未成交部分，都记录到失败订单中
            if self._should_record_order_error(order_res):
                if order_res and self._has_partial_fill(order_res):
                    row['实际下单量'] = float(order_res['origQty']) - float(order_res['executedQty'])
                order_error_list.append(row)
            # 收集成功（含部分成交）的回执，供上层归档
            if order_res and isinstance(order_res, dict) and order_res.get('status') in ('FILLED', 'PARTIALLY_FILLED'):
                if 'symbol_type' not in order_res:
                    order_res['symbol_type'] = symbol_type
                order_success_list.append(order_res)

            time.sleep(0.05)  # OKX 下单接口有频率限制，稍微歇一下

        return order_success_list, order_error_list

    def place_spot_orders_bulk2(self, orders_df, slip_rate=0.015):
        """:return: (success_list, final_failures) 成功（含部分成交）回执 + 重试后仍失败的原始订单行"""
        self.get_spot_ticker_price_series()
        success_list, error_orders = self.prepare_order_params_and_place_order(orders_df, 'spot', slip_rate)
        final_failures = []

        # 一批次下单结束，再重试一次
        if error_orders:
            error_orders = pd.DataFrame(error_orders)
            error_orders.index.name = 'symbol'
            error_orders = error_orders.reset_index()
            retry_success, final_failures = self.prepare_order_params_and_place_order(error_orders, 'spot', slip_rate)
            success_list.extend(retry_success)

        return success_list, final_failures

    def place_swap_orders_bulk2(self, orders_df, slip_rate=0.015):
        """:return: (success_list, final_failures) 成功（含部分成交）回执 + 重试后仍失败的原始订单行"""
        self.get_swap_ticker_price_series()
        success_list, error_orders = self.prepare_order_params_and_place_order(orders_df, 'swap', slip_rate)
        final_failures = []

        # 一批次下单结束，再重试一次
        if error_orders:
            error_orders = pd.DataFrame(error_orders)
            error_orders.index.name = 'symbol'
            error_orders = error_orders.reset_index()
            retry_success, final_failures = self.prepare_order_params_and_place_order(error_orders, 'swap', slip_rate)
            success_list.extend(retry_success)

        return success_list, final_failures

    # ====================================================================================================
    # ** 账户与持仓 **
    # ====================================================================================================
    def _fetch_account_balance(self) -> dict:
        response = retry_wrapper(
            self.exchange.private_get_account_balance, func_name='获取OKX账户资产'
        )
        data = (response or {}).get('data', [])
        return data[0] if data else {}

    def get_spot_position_df(self) -> pd.DataFrame:
        """
        获取现货（交易账户内各币种资产）持仓。
        与 BinanceClient 保持相同结构：symbol 列为资产名（如 BTC），非交易对
        """
        balance_data = self._fetch_account_balance()
        details = balance_data.get('details', [])

        rows = []
        for detail in details:
            avail = _safe_float(detail, 'availBal', 0.0)
            frozen = _safe_float(detail, 'frozenBal', 0.0)
            amount = avail + frozen
            if amount == 0:
                continue
            rows.append({'symbol': detail.get('ccy', ''), '当前持仓量': amount, 'free': avail})

        position_df = pd.DataFrame(rows, columns=['symbol', '当前持仓量', 'free'])
        position_df['仓位价值'] = None  # 设置默认值
        return position_df

    def get_swap_position_df(self) -> pd.DataFrame:
        """
        获取OKX账户的实际合约持仓（币数量），与 BinanceClient 相同结构：

                        当前持仓量   均价  持仓盈亏  当前标记价格  仓位价值
        symbol(instId)
        """
        response = retry_wrapper(
            self.exchange.private_get_account_positions, params={'instType': 'SWAP'},
            func_name='获取OKX合约持仓'
        )
        data = (response or {}).get('data', [])

        columns = ['symbol', '当前持仓量', '均价', '持仓盈亏', '当前标记价格', '仓位价值']
        rows = []
        for pos in data:
            if pos.get('instType', '') != 'SWAP':
                continue
            contracts = _safe_float(pos, 'pos', 0.0)  # 净持仓模式下，正数为多头，负数为空头
            if contracts == 0:
                continue
            inst_id = pos.get('instId', '')
            amount = self.contracts_to_coins(inst_id, contracts)
            mark_price = _safe_float(pos, 'markPx', 0.0)
            rows.append({
                'symbol': inst_id,
                '当前持仓量': amount,
                '均价': _safe_float(pos, 'avgPx', 0.0),
                '持仓盈亏': _safe_float(pos, 'upl', 0.0),
                '当前标记价格': mark_price,
                '仓位价值': amount * mark_price,
            })

        if not rows:
            return pd.DataFrame(columns=columns)

        position_df = pd.DataFrame(rows)
        position_df.set_index('symbol', inplace=True)
        return position_df[['当前持仓量', '均价', '持仓盈亏', '当前标记价格', '仓位价值']]

    def update_swap_account(self) -> dict:
        """
        构造与 Binance 兼容的合约账户信息结构（assets + positions）。
        assets 来自交易账户余额，positions 来自当前持仓。
        """
        balance_data = self._fetch_account_balance()
        assets = []
        for detail in balance_data.get('details', []):
            assets.append({
                'asset': detail.get('ccy', ''),
                'walletBalance': str(_safe_float(detail, 'cashBal', 0.0)),
                'crossWalletBalance': str(_safe_float(detail, 'cashBal', 0.0)),
                'maxWithdrawAmount': str(_safe_float(detail, 'availBal', 0.0)),
            })

        response = retry_wrapper(
            self.exchange.private_get_account_positions, params={'instType': 'SWAP'},
            func_name='获取OKX合约持仓'
        )
        positions = []
        for pos in (response or {}).get('data', []):
            inst_id = pos.get('instId', '')
            positions.append({
                'symbol': inst_id,
                'leverage': int(_safe_float(pos, 'lever', 1.0)),
                'positionAmt': str(self.contracts_to_coins(inst_id, _safe_float(pos, 'pos', 0.0))),
                'notional': str(_safe_float(pos, 'notionalUsd', 0.0)),
                'maxNotional': '',
            })

        self.swap_account = {'assets': assets, 'positions': positions}
        return self.swap_account

    def get_swap_account(self, require_update: bool = False) -> dict:
        if self.swap_account is None or require_update:
            self.update_swap_account()
        return self.swap_account

    def get_swap_usdt_balance(self, asset='USDT') -> float:
        balance_data = self._fetch_account_balance()
        for detail in balance_data.get('details', []):
            if detail.get('ccy', '') == asset:
                return _safe_float(detail, 'cashBal', 0.0)
        return 0

    def get_account_overview(self):
        """
        根据最新行情价格与账户资产，计算账户的现金、持仓价值和负债。
        返回结构与 Binance 的统一账户（PortfolioMarginClient）完全一致。

        权益口径说明：
        - account_equity 直接取 OKX 的 totalEq（账户美金层面总权益，含未实现盈亏与负债）
        - spot_equity   = 现货持仓价值 + 基础保证金币种余额
        - swap_equity   = account_equity - spot_equity（残差口径，保证两者之和恒等于总权益）
        """
        spot_ticker_data = self.fetch_spot_ticker_price()
        spot_ticker = {_['symbol']: float(_['price']) for _ in spot_ticker_data}

        balance_data = self._fetch_account_balance()
        swap_position_df = self.get_swap_position_df()
        logger.ok('获取账户资产数据完成')

        if not balance_data:
            return {
                'usdt_balance': 0,
                'negative_balance': 0,
                'account_pnl': 0,
                'account_equity': 0,
                'seed_coin_spot_equity': 0,
                'spot_assets': {
                    'assets_pos_value': {},
                    'assets_amount': {},
                    'usdt': 0,
                    'equity': 0,
                    'dust_spot_df': pd.DataFrame(),
                    'spot_position_df': pd.DataFrame()
                },
                'swap_assets': {
                    'assets_pos_value': {},
                    'assets_amount': {},
                    'assets_pnl': {},
                    'usdt': 0,
                    'equity': 0,
                    'swap_position_df': pd.DataFrame()
                }
            }

        logger.info('准备处理资产数据...')
        account_equity = _safe_float(balance_data, 'totalEq', 0.0)  # 账户总权益（USD）
        account_pnl = _safe_float(balance_data, 'upl', 0.0)  # 未实现盈亏

        # ===整理各币种资产
        balance_rows = []
        spot_usdt_balance = 0.0
        negative_balance = 0.0
        for detail in balance_data.get('details', []):
            ccy = detail.get('ccy', '')
            cash_bal = _safe_float(detail, 'cashBal', 0.0)
            avail = _safe_float(detail, 'availBal', 0.0)

            if ccy in self.base_coins:
                # 基础保证金币种（锚定 1:1 美元）
                spot_usdt_balance += (cash_bal if cash_bal > 0 else 0)
                self.base_coin_amount[ccy] = (cash_bal if cash_bal > 0 else 0)
                if cash_bal < 0:
                    negative_balance += cash_bal
                continue

            inst_id = f'{ccy}-USDT'
            price = spot_ticker.get(inst_id, 0.0)
            if cash_bal < 0:  # 跨币种保证金模式下的负余额即负债
                negative_balance += cash_bal * price
                continue

            balance_rows.append({
                'symbol': inst_id,
                '当前持仓量': cash_bal,
                'free': avail,
                '仓位价值': cash_bal * price,
            })

        balance = pd.DataFrame(balance_rows, columns=['symbol', '当前持仓量', 'free', '仓位价值'])

        # 过滤掉不含报价的币
        balance = balance[balance['仓位价值'] != 0]
        # 仓位价值 小于 5U，无法下单的碎币，单独记录
        dust_spot_df = balance[balance['仓位价值'] < 5]
        # 过滤掉仓位价值 小于 5U
        balance = balance[balance['仓位价值'] > 5]
        # 去除掉已经下架的币种，避免下架币的资金影响到当前账户的配置
        if 'spot' not in self.market_info:
            self.get_market_info(symbol_type='spot')
        balance = balance[balance['symbol'].isin(self.market_info['spot']['symbol_list'])]

        # 现货净值 = 现货仓位价值总和 + 基础保证金
        spot_equity = balance['仓位价值'].sum() + spot_usdt_balance
        seed_coin_spot_equity = 0

        # =====处理现货持仓列表信息
        if self.seed_coins:
            seed_coin_spot_equity = balance[balance['symbol'].isin(self.seed_coins)]['仓位价值'].sum()
            balance = balance[~balance['symbol'].isin(self.seed_coins)]

        # 币种 : 价值
        spot_assets_pos_dict = {row['symbol']: row['仓位价值'] for row in balance.to_dict(orient='records')}
        # 币种 : 数量
        spot_asset_amount_dict = {row['symbol']: row['当前持仓量'] for row in balance.to_dict(orient='records')}

        # =====处理合约持仓列表信息
        swap_position_df.reset_index(inplace=True)
        # 判断是否需要过滤掉套利仓位
        if self.seed_coins:
            swap_position_df = swap_position_df[~swap_position_df['symbol'].isin(self.seed_coins)]

        swap_assets_pos_dict = {row['symbol']: row['仓位价值'] for row in swap_position_df.to_dict(orient='records')}
        swap_asset_amount_dict = {row['symbol']: row['当前持仓量'] for row in swap_position_df.to_dict(orient='records')}
        swap_asset_pnl_dict = {row['symbol']: row['持仓盈亏'] for row in swap_position_df.to_dict(orient='records')}

        # 处理完成之后再设置index
        swap_position_df.set_index('symbol', inplace=True)

        # 合约净值 = 账户总权益 - 现货净值（残差口径，两者之和恒等于账户总权益）
        swap_equity = account_equity - spot_equity

        logger.ok('处理资产数据完成')

        return {
            'usdt_balance': spot_usdt_balance,
            'negative_balance': negative_balance,
            'account_pnl': account_pnl,
            'account_equity': account_equity,
            'seed_coin_spot_equity': seed_coin_spot_equity,
            'spot_assets': {
                'assets_pos_value': spot_assets_pos_dict,
                'assets_amount': spot_asset_amount_dict,
                'usdt': spot_usdt_balance,
                'equity': spot_equity,
                'dust_spot_df': dust_spot_df,
                'spot_position_df': balance
            },
            'swap_assets': {
                'assets_pos_value': swap_assets_pos_dict,
                'assets_amount': swap_asset_amount_dict,
                'assets_pnl': swap_asset_pnl_dict,
                'usdt': 0,
                'equity': swap_equity,
                'swap_position_df': swap_position_df
            }
        }

    # ====================================================================================================
    # ** Binance 兼容方法 **
    # OKX 统一交易账户不需要这些操作，实现为 no-op 仅保持接口一致
    # ====================================================================================================
    def transfer_u_from_spot_to_swap(self, amount, asset='USDT'):
        logger.debug(f'OKX 统一交易账户共享保证金，无需划转 {amount} {asset}，跳过')

    def transfer_u_from_swap_to_spot(self, amount, asset='USDT'):
        logger.debug(f'OKX 统一交易账户共享保证金，无需划转 {amount} {asset}，跳过')

    def transfer_bnb_for_dust_spot(self, dust_spot):
        logger.debug('OKX 不支持小额资产兑换BNB功能，跳过')

    def replenish_bnb(self, buy_bnb_value, is_use_spot=True):
        logger.debug('OKX 不使用BNB抵扣手续费，跳过补充BNB操作')
        return 0

    def adjust_base_margin(self):
        if len(self.base_coins) > 1:
            logger.warning('OKX 客户端暂不支持基础保证金动态调整功能，跳过')

    def collect_asset(self, asset='USDT'):
        logger.debug('OKX 统一交易账户无需资金归集，跳过')

    def repay_interest(self):
        # OKX 跨币种保证金模式的负债利息由交易所自动处理，无需手动还款
        pass

    def get_base_coin_equity(self):
        account_overview = self.get_account_overview()
        negative_balance = account_overview.get('negative_balance', 0)

        spot_ticker_data = self.fetch_spot_ticker_price()
        spot_ticker = {_['symbol']: float(_['price']) for _ in spot_ticker_data}
        account_equity = sum([amount * spot_ticker.get(f'{base_coin}-USDT', 1)
                              for base_coin, amount in self.base_coin_amount.items()])
        account_equity += negative_balance
        return account_equity or 0

    def fetch_transfer_history(self, start_time=None):
        """OKX 统一交易账户内无现货/合约划转，返回空数据"""
        return pd.DataFrame()

    def fetch_spot_trades(self, symbol, end_time) -> pd.DataFrame:
        """
        获取现货成交历史，输出与 BinanceClient 相同结构：
        ['time', 'symbol', 'price', 'qty', 'quoteQty', 'commission', 'commissionAsset', '方向']
        """
        params = {
            'instType': 'SPOT',
            'instId': symbol,
            'end': str(int(time.mktime(end_time.timetuple())) * 1000),
            'limit': '100',
        }
        trades = retry_wrapper(self.exchange.private_get_trade_fills, params=params,
                               func_name='获取币种历史订单信息', if_exit=False)
        data = (trades or {}).get('data', [])
        if not data:
            return pd.DataFrame()

        trades_df = pd.DataFrame(data)
        trades_df['price'] = pd.to_numeric(trades_df['fillPx'], errors='coerce')
        trades_df['qty'] = pd.to_numeric(trades_df['fillSz'], errors='coerce')
        trades_df['quoteQty'] = trades_df['price'] * trades_df['qty']
        trades_df['commission'] = pd.to_numeric(trades_df['fee'], errors='coerce').abs()
        trades_df['方向'] = np.where(trades_df['side'] == 'buy', 1, -1)
        trades_df.rename(columns={'ts': 'time', 'instId': 'symbol', 'feeCcy': 'commissionAsset'}, inplace=True)
        return trades_df[['time', 'symbol', 'price', 'qty', 'quoteQty', 'commission', 'commissionAsset', '方向']]

    @classmethod
    def get_dummy_client(cls) -> 'OkxClient':
        return cls()
