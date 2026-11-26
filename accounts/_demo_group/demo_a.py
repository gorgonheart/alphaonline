# ====================================================================================================
# ** 账户配置 **
# ====================================================================================================
account_config = {
    'apiKey': '',
    'secret': '',
    'account_type': '普通账户',
    'exchange_name': 'binance',  # 两个样例均使用 Binance；行情源需与交易所一致。
    'wechat_webhook_url': 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=',  # 本账户统计机器人，组汇总机器人在 _manage.py 中配置。
    'hour_offset': '20m',  # 组内成员 hour_offset 必须一致
    'if_use_bnb_burn': False,
    'if_transfer_bnb': False,
}

# ====================================================================================================
# ** 策略细节配置 **
# 案例策略，需要自己探索，不保证可用
# ====================================================================================================
strategy_name = '成员A合约多头示例'
min_kline_num = 0
get_kline_num = 1500  # 获取多少根K线。这里跟策略日频和小时频影响。日线策略，代表多少根日线k。小时策略，代表多少根小时k
strategy_config = {
    'name': 'FixedRatioStrategy',
    'hold_period': '1H',
    'params': {'cap_ratios': [1.0]},
}

# 策略池
strategy_pool = [{
    'name': '合约多头',
    'strategy_list': [{
        'strategy': 'Strategy',  # 框架声明式默认标签，使用通用选币逻辑
        'use_custom_func': False,
        'hold_period': '1H',
        'offset_list': [0],
        'market': 'swap_swap',
        'cap_weight': 1,
        'long_cap_weight': 1,
        'short_cap_weight': 0,
        'long_select_coin_num': 1,
        'short_select_coin_num': 0,
        'factor_list': [('PctChange', False, 24, 1)],  # 已有24小时涨跌幅因子
        'filter_list': [],
    }],
}]

leverage = 1  # 杠杆数。我看哪个赌狗要把这里改成大于1的。高杠杆如梦幻泡影。不要想着一夜暴富，脚踏实地赚自己该赚的钱。
black_list = []  # 拉黑名单，永远不会交易。不喜欢的币、异常的币。例：LUNA-USDT, 这里与实盘不太一样，需要有'-'
white_list = []  # 如果不为空，即只交易这些币，只在这些币当中进行选币。例：LUNA-USDT, 这里与实盘不太一样，需要有'-'
#󠀂󠁥󠀹󠁢󠁥󠀹󠁡󠁥󠀵󠀸󠁤󠀹󠁡󠁥󠀸󠀸󠀷󠁢󠀴󠀲󠀰󠀳󠀳󠀳󠀲󠀳󠀲󠀳󠀹󠀳󠀷󠁿
