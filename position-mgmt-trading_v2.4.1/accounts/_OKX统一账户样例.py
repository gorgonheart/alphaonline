# ====================================================================================================
# ** OKX 账户配置样例 **
# 使用前提：
# 1. 数据中心运行 OKX 版本（realtime_okx.py），并把 config.py / config.json 中的
#    realtime_data_path 指向数据中心的 data_okx 目录
# 2. OKX 账户开通「跨币种保证金模式」(账户模式 acctLv=3)，框架启动自检时也会自动尝试开启
# 3. OKX 的 API 除了 apiKey/secret 之外还需要 passphrase，填在 password 字段
#󠀂󠁥󠀹󠁢󠁥󠀹󠁡󠁥󠀵󠀸󠁤󠀹󠁡󠁥󠀸󠀸󠀷󠁢󠀴󠀲󠀰󠀳󠀳󠀳󠀲󠀳󠀲󠀳󠀹󠀳󠀷󠁿
# 注意：
# - OKX 账户在框架内统一按「统一账户」处理（现货与合约共享保证金，无需划转）
# - 黑白名单直接配置 OKX 的 instId（如 LUNA-USDT），框架会自动展开成现货+永续
# ====================================================================================================
account_config = {
    # 指定交易所为 OKX（不填默认 binance）
    'exchange_name': 'okx',

    # 交易所API配置
    'apiKey': '',
    'secret': '',
    'password': '',  # OKX 的 passphrase，必填
    # 'demo': True,  # 使用OKX模拟盘时打开

    # ++++ 分钟偏移功能 ++++
    'hour_offset': '0m',  # 分钟偏移设置，配置必须是数据中心 interval 的倍数。默认：0m，表示不偏移

    # ++++ 企业微信机器人功能 ++++
    "wechat_webhook_url": 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=',
    'max_one_order_amount': 100,

    # OKX 仅支持统一交易账户，account_type 配置会被自动按【统一账户】处理
    "account_type": '统一账户',
}

# ====================================================================================================
# ** 策略细节配置 **
# ====================================================================================================
min_kline_num = 168
get_kline_num = 999

strategy_name = 'OKX测试'  # 当前账户运行策略的名称。可以自己任意取

strategy_config = {
    'name': 'FixedRatioStrategy',  # *必填。使用什么策略，这里是轮动策略
    'hold_period': '1H',  # *必填。聚合后策略持仓周期
    'params': {
        'cap_ratios': [1],
    },
}
strategy_pool = [
    dict(
        name='示例策略',
        strategy_list=[
            {
                "strategy": "Strategy_基础框架",
                "offset_list": [0],
                "hold_period": "1H",
                "market": "swap",
                'cap_weight': 1,
                'long_cap_weight': 1,
                'short_cap_weight': 1,
                'long_select_coin_num': 0.1,
                'short_select_coin_num': 0.1,
                'use_custom_func': False,
                "factor_list": [('QuoteVolumeMean', True, 96, 1)],
                "filter_list": [],
            }
        ],
    ),
]

black_list = []  # 拉黑名单，永远不会交易。OKX 配置 instId，例：LUNA-USDT
white_list = []  # 如果不为空，即只交易这些币。OKX 配置 instId，例：BTC-USDT
