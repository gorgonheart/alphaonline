"""Inactive MODEL_ROBUST40 v1 example. No credentials; not auto-discovered.
Use one unique persistent state path per account; validate live overlap before activation.
The portfolio rebalance mode is nested in account_config so children keep RebAlways.
"""

account_config = {'apiKey': '',
 'secret': '',
 'hour_offset': '55m',
 'rebalance_mode': {'mode': 'RebRobust40', 'params': {'min_order_usdt_ratio': 0.4, 'execution_cap': 2.45}},
 'account_type': '统一账户',
 'buy_bnb_value': 0,
 'if_use_bnb_burn': False,
 'if_transfer_bnb': False,
 'order_spot_money_limit': 10,
 'order_swap_money_limit': 5,
 'filter_bstocks': True}

strategy_name = 'MODEL_ROBUST40_V1_55m'

strategy_config = {'name': 'ModelRobust40Strategy',
 'hold_period': '1H',
 'params': {'enable_live_hooks': True, 'factor_list': [('Bias', False, 48, 1), ('Bias', False, 144, 1), ('Bias', False, 216, 1)],
            'parent': {'method': 'native',
                       'gross_cap': 2.5,
                       'research_inputs': 'E:/quant/position-mgmt_v2.3.0/research_20261019_cross_strategy/inputs',
                       'child_returns': 'E:/quant/position-mgmt_v2.3.0/research_20261019_momentum_hedge/tables/CHILD_RETURNS.pkl',
                       'risk_definitions': ['down', 'up'],
                       'momentum_60': True,
                       'conditional_risk_path': 'E:/quant/position-mgmt_v2.3.0/research_20261020_sharpe65/inputs/conditional_beta.pkl',
                       'factor_list': [('Bias', False, 48, 1),
                                       ('Bias', False, 144, 1),
                                       ('Bias', False, 216, 1)],
                       'cap_ratios': [0.457983193277,
                                      0.457983193277,
                                      0.623781512606,
                                      0.274789915966,
                                      0.245462184874,
                                      0.143442622951,
                                      0.243442622951,
                                      0.243442622951,
                                      0.243442622951,
                                      0.06762295082,
                                      0.243442622951,
                                      0.200163934426,
                                      0.15,
                                      0.32],
                       'direction_list': ['long',
                                          'long',
                                          'long',
                                          'long',
                                          'long',
                                          'short',
                                          'short',
                                          'short',
                                          'short',
                                          'short',
                                          'short',
                                          'short',
                                          'short',
                                          'long'],
                       'rotation_indices': [0, 1],
                       'dynamic_groups': [{'indices': [3, 13],
                                           'direction': 'long',
                                           'mode': 'both',
                                           'period_hours': 3456,
                                           'trigger_threshold': 0.786,
                                           'rebalance_duration_hours': 21},
                                          {'indices': [6, 12, 9, 11],
                                           'direction': 'short',
                                           'mode': 'both',
                                           'period_hours': 3456,
                                           'trigger_threshold': 0.786,
                                           'rebalance_duration_hours': 21}],
                       'rebalance_anchor_time': '2021-01-01 00:00:00',
                       'short_single_coin_max_ratio': 0.05},
            'runtime': {'minute': 55,
                        'bootstrap_path': 'research/model_robust40_v1/55m/runtime_bootstrap.pkl',
                        'state_path': 'data/robust40_state/ACCOUNT_NAME_55m.pkl'}}}

simulator_config = {'account_type': '统一账户',
 'initial_usdt': 10000,
 'margin_rate': 0.05,
 'swap_c_rate': 0.0006,
 'spot_c_rate': 0.001,
 'swap_min_order_limit': 5,
 'spot_min_order_limit': 10,
 'avg_price_col': 'avg_price_1m',
 'unified_time': '2017-01-01'}

required_history_start = '2025-10-28 00:55:00'
precompute_factor_configs = {'动量210401_双项异常隔离V2': [13608, 5421, 3509, 12600]}
get_kline_num = 5000  # Fixed-history mode keeps all retained PKL bars.
min_kline_num = 1
leverage = 1
black_list = []
white_list = []

strategy_pool = [
    dict(
        name='大学生实盘多头_后置过滤',
        strategy_list=[
            {
                "strategy": "Strategy_大学生",
                "offset_list": range(1, 24, 1),
                "hold_period": '24H',
                "is_use_spot": False,
                'cap_weight': 0.5,
                'long_cap_weight': 1,
                'short_cap_weight': 0,
                'long_select_coin_num': 0.2,
                'short_select_coin_num': 0,
                "factor_list": [
                    ('CirculatingMcap', True, 1, 1),  # 多头因子名（和factors文件中相同），排序方式，参数，权重。
                ],
                "filter_list": [
                    ('ZfStd', 24, 'pct:<0.8'),
                    ('BiasV1', 2760, 'pct:<0.3'),
                    ('ILLQStdBbi45208', 2856, 'pct:>=0.5', False),
                    ('111_输入隔离', 26, 'pct:>=0.2'),
                    ('新币', 1, 'val:>168'),
                ],
                "filter_list_post": [
                    ('ZfMeanQ', x, 'val:>0.5'),
                ],
                "use_custom_func": False,  # 使用系统内置因子计算、过滤函数
            } for x in [432, 576]
        ],
        # 配置再择时之后，可以使用 re_timing.py 进行再择时的资金曲线模拟
        # re_timing={'name': 'Bolling2', 'params': [96]},  # 可选，配置再择时策略
    ),

    dict(
        name='大学生实盘多头',
        strategy_list=[
            {
                "strategy": "Strategy_大学生",
                "offset_list": range(1, 24, 1),
                "hold_period": '24H',
                "is_use_spot": False,
                'cap_weight': 1,
                'long_cap_weight': 1,
                'short_cap_weight': 0,
                'long_select_coin_num': 0.2,
                'short_select_coin_num': 0,
                "factor_list": [
                    ('CirculatingMcap', True, 1, 1),  # 多头因子名（和factors文件中相同），排序方式，参数，权重。
                ],
                "filter_list": [
                    ('ZfStd', 24, 'pct:<0.8'),
                    ('BiasV1', 2760, 'pct:<0.3'),
                    ('ILLQStdBbi45208', 2856, 'pct:>=0.5', False),
                    ('111_输入隔离', 26, 'pct:>=0.2'),
                    ('新币', 1, 'val:>168'),
                ],
                # "filter_list_post": [
                #     ('ZfMeanQ', 576, 'val:>0.5'),
                # ],
                "use_custom_func": False,  # 使用系统内置因子计算、过滤函数
            }
        ],
        # 配置再择时之后，可以使用 re_timing.py 进行再择时的资金曲线模拟
        # re_timing={'name': 'Bolling2', 'params': [96]},  # 可选，配置再择时策略
    ),

    dict(
        name='GA实盘多头0317',
        strategy_list=[
            {
                'strategy': 'Strategy_VolumeMaxBias',
                'offset_list': range(0, 1),
                'hold_period': '1H',
                'market': 'mix_swap',
                'cap_weight': 1,
                'long_cap_weight': 1,
                'short_cap_weight': 0,
                'long_select_coin_num': 999,
                'short_select_coin_num': 0,
                'factor_list': [('QuoteVolumeMean修正', True, 168, 1)],
                # 实盘生效日是 '2026-06-01'，回测起点是 2021-01-01，故改为 '2021-01-01'
                'filter_list': [('VolumeMaxBias_进出场阈值分离官方', (720, 0.1, 0.9, '2025-11-28'), 'val:==1', False),
                                ('CirculatingMcap', 1, 'pct:>0.5', False)],
                'filter_list_post': [('CorrBTC', 892, 'pct:>0.5', False)],
                'use_custom_func': False,
            }
        ],
    ),

    dict(
        name='双均线trial1677',
        strategy_list=[
            {
                "strategy": "双均线",
                "offset_list": list(range(0, 24, 1)),
                "hold_period": '24H',
                "is_use_spot": False,
                'cap_weight': 1.0,
                'long_cap_weight': 1,
                'short_cap_weight': 0,
                'long_select_coin_num': 0.1,
                'short_select_coin_num': 0,

                # Ranking factor — x=300 (median config from base_strategies[0])
                "factor_list": [
                    ('双均线', False, x, 1),
                ],

                # Pre-filters (pinned only, n_dynamic=0)
                "filter_list": [
                    ('SpotSwapBoth', 1, 'val:<0.5'),
                    ('资金费率绝对值MAX', 48, 'val:<=0.015'),
                ],

                # Post-filters (2 dynamic)
                "filter_list_post": [
                    ('CorrBTC', 344, 'pct:>0.5', False),
                    ('BiasQ', 420, 'pct:<0.7', False),
                ],

                "use_custom_func": False,
            } for x in [336, 432]  # , 384
        ],
    ),

    dict(
        name='动量实盘多头',
        strategy_list=[
            # ===========================================================
            # !!! 实盘前先回测一下，不要无脑跑案例策略，没人能保证案例策略能赚钱 !!!
            # 以下配置非官方提供的案例策略，这里只是举例如何配置，具体配置请自行处理
            # ===========================================================
            {
                "strategy": "线上实盘多头",
                "offset_list": range(1, 4, 1),
                "hold_period": "4H",
                "is_use_spot": False,
                'cap_weight': 1 / 4,
                'long_cap_weight': 1,
                'short_cap_weight': 0,
                'long_select_coin_num': 0.1,
                'short_select_coin_num': 0,
                'long_factor_list': [('动量210401_双项异常隔离V2', False, x, 1)],
                'long_filter_list': [('ILLQStdBbi45208', y, 'pct:<=0.2', False), ('新币', 1, 'val:>168')],
                'long_filter_list_post': [('111_输入隔离', 13, 'val:>=0.000005')],
                # "short_factor_list": [
                #     ('CciV3', False, 168, 1)  # 因子名（和factors文件中相同），排序方式，参数，权重。
                #     # ('动量因子21按异常切分', False, x, 1)
                # ],

                "use_custom_func": False,  # 使用系统内置因子计算、过滤函数
            } for x, y in zip([13608, 5421, 3509, 12600], [2184, 2448, 1104, 264])
        ],
        # 配置再择时之后，可以使用 re_timing.py 进行再择时的资金曲线模拟
        re_timing={'name': 'Bolling2multi', 'params': [[96, 158, 167, 176, 208, 217, 226, 235]]},
        # re_timing={'name': 'Bolling2multi', 'params': [[48, 96, 120, 144]]},
    ),

    dict(
        name='CciV3收益形空头',
        strategy_list=[
            # ===========================================================
            # !!! 实盘前先回测一下，不要无脑跑案例策略，没人能保证案例策略能赚钱 !!!
            # 以下配置非官方提供的案例策略，这里只是举例如何配置，具体配置请自行处理
            # ===========================================================
            {
                "strategy": "动量空头",
                "offset_list": range(1, 16, 1),
                "hold_period": "16H",
                "is_use_spot": False,
                'cap_weight': 1 / 4,
                'long_cap_weight': 0,
                'short_cap_weight': 1,
                'long_select_coin_num': 0,
                'short_select_coin_num': 0.05,

                "short_factor_list": [
                    ('CciV3', False, x, 1),  # 多头因子名（和factors文件中相同），排序方式，参数，权重。
                ],
                "short_filter_list": [
                    ('QuoteVolumeMean修正', y, 'pct:<0.2', False),
                    ('CirculatingMcap', 1, 'pct:>=0.1'),
                    ('111_输入隔离', a, 'pct:<=0.8'),
                ],
                "filter_list_post": [
                    ('ZfMeanQ', z, 'val:<0.5'),
                ],
                "use_custom_func": False,  # 使用系统内置因子计算、过滤函数
            } for x, y, z, a in zip([1176, 10248, 10248, 5208], [504, 504, 168, 168], [192, 192, 360, 360], [26, 26, 36, 36])
        ],
    ),

    dict(
        name='minmax59614空头优化寻参',
        strategy_list=[
            # ===========================================================
            # !!! 实盘前先回测一下，不要无脑跑案例策略，没人能保证案例策略能赚钱 !!!
            # 以下配置非官方提供的案例策略，这里只是举例如何配置，具体配置请自行处理
            # ===========================================================
            {
                "strategy": "minmax59614空头",
                "offset_list": range(1, 16, 1),
                "hold_period": '16H',
                "is_use_spot": False,
                # 资金权重。程序会自动根据这个权重计算你的策略占比，具体可以看1.8的直播讲解
                'cap_weight': 1 / 4,
                'long_cap_weight': 0,
                'short_cap_weight': 1,
                'long_select_coin_num': 0,
                'short_select_coin_num': 0.5,
                # 选币因子信息列表，用于`2_选币_单offset.py`，`3_计算多offset资金曲线.py`共用计算资金曲线
                "factor_list": [
                    ('MinMax', False, x, 1),  # 元组四要素解析：
                                              # 1.因子名称（Cci对应factors/Cci.py中的<mcsymbol name="signal" filename="Cci.py"
                                              # 2.排序方式（False=降序=从大到小）(True=升序)
                                              # 3.周期参数x（遍历240/432/600小时周期）
                                              # 4.因子权重1.0
                ],
                "filter_list": [
                    ('QuoteVolumeMean修正', 168, 'pct:<0.2', False),
                    ('CirculatingMcap', 1, 'pct:>0.2', False),
                    ('Atr', a, 'pct:<0.3', False),
                    ('Bias (2)', x, 'val:<0', False),
                    ('Bias (2)', x, 'val:>-0.15', False),
                ],
                "filter_list_post": [
                    ('ZfMeanQ', y, 'val:<0.7'),
                    ('HoursSinceSpotAndSwap', 1, 'val:>10'),
                ],
                "use_custom_func": False,  # 使用系统内置因子计算、过滤函数
            } for a, x, y in zip([168, 168, 3552, 3552], [240, 360, 240, 360], [240, 816, 240, 816])
        ],
    ),

    dict(
        name='minmax59614空头优化寻参后过滤',
        strategy_list=[
            # ===========================================================
            # !!! 实盘前先回测一下，不要无脑跑案例策略，没人能保证案例策略能赚钱 !!!
            # 以下配置非官方提供的案例策略，这里只是举例如何配置，具体配置请自行处理
            # ===========================================================
            {
                "strategy": "minmax59614空头",
                "offset_list": range(1, 16, 1),
                "hold_period": '16H',
                "is_use_spot": False,
                # 资金权重。程序会自动根据这个权重计算你的策略占比，具体可以看1.8的直播讲解
                'cap_weight': 1 / 2,
                'long_cap_weight': 0,
                'short_cap_weight': 1,
                'long_select_coin_num': 0,
                'short_select_coin_num': 0.4,
                # 选币因子信息列表，用于`2_选币_单offset.py`，`3_计算多offset资金曲线.py`共用计算资金曲线
                "factor_list": [
                    ('MinMax', False, x, 1),  # 元组四要素解析：
                                              # 1.因子名称（Cci对应factors/Cci.py中的<mcsymbol name="signal" filename="Cci.py"
                                              # 2.排序方式（False=降序=从大到小）(True=升序)
                                              # 3.周期参数x（遍历240/432/600小时周期）
                                              # 4.因子权重1.0
                ],
                "filter_list": [
                    ('CirculatingMcap', 1, 'pct:>0.2', False),
                    ('QuoteVolumeMean修正', 168, 'pct:<0.2', False),
                    ('Bias (2)', x, 'val:<0', False),
                    ('Bias (2)', x, 'val:>-0.2', False),
                ],
                "filter_list_post": [
                    ('ZfMeanQ', y, 'val:<0.7'),
                    ('Atr', a, 'pct:<0.95', False),
                    ('HoursSinceSpotAndSwap', 1, 'val:>10'),
                ],
                "use_custom_func": False,  # 使用系统内置因子计算、过滤函数
            } for a, x, y in zip([168, 168], [240, 360], [240, 816])
        ],
    ),

    dict(
        name='Cci选0.2形空头',
        strategy_list=[
            # ===========================================================
            # !!! 实盘前先回测一下，不要无脑跑案例策略，没人能保证案例策略能赚钱 !!!
            # 以下配置非官方提供的案例策略，这里只是举例如何配置，具体配置请自行处理
            # ===========================================================
            {
                "strategy": "选0.2",
                "offset_list": range(1, 16, 1),
                "hold_period": "16H",
                "is_use_spot": False,
                'cap_weight': 1 / 3,
                'long_cap_weight': 0,
                'short_cap_weight': 1,
                'long_select_coin_num': 0,
                'short_select_coin_num': 0.2,

                "short_factor_list": [
                    ('Cci异常隔离168_因果版', False, x, 1),  # 多头因子名（和factors文件中相同），排序方式，参数，权重。
                ],
                "short_filter_list": [
                    ('QuoteVolumeMean修正', 168, 'pct:<0.2', False),
                    ('CirculatingMcap', 1, 'pct:>=0.1'),
                ],
                "filter_list_post": [
                    ('ZfMeanQ', 312, 'val:<0.7'),
                ],
                "use_custom_func": False,  # 使用系统内置因子计算、过滤函数
            } for x in (240, 816, 1536)
        ],
        # # 配置再择时之后，可以使用 re_timing.py 进行再择时的资金曲线模拟
        # re_timing={'name': 'Bolling2', 'params': [334]}  # 可选，配置再择时策略
    ),

    dict(
        name='落九天',
        strategy_list=[
            # ===========================================================
            # !!! 实盘前先回测一下，不要无脑跑案例策略，没人能保证案例策略能赚钱 !!!
            # 以下配置非官方提供的案例策略，这里只是举例如何配置，具体配置请自行处理
            # ===========================================================
            {
                "strategy": "魔改落九天",
                "offset_list": range(0, 1, 1),
                "hold_period": "1H",
                "is_use_spot": False,
                'cap_weight': 1 / 3,
                'long_cap_weight': 0,
                'short_cap_weight': 1,
                'long_select_coin_num': 0,
                'short_select_coin_num': 999,
                "factor_list": [
                    ('QuoteVolumeMean修正', True, 168, 1),
                ],
                "filter_list": [
                    ('新币落九天用的', 1, 'val:<490'),
                    ('QuoteVolumeMean修正', 50, 'pct:<0.8', False),
                ],
                "filter_list_post": [
                    ('ZfMeanQ', x, 'val:<0.5'),  # 最优300多
                    # ('ZfEmaQ', x, 'val:<0.5'),
                    ('PctChangeMax', 12, 'val:<0.04'),
                ],
                "use_custom_func": False,
            } for x in (72, 120, 192)  # , 384
        ],
        # # 配置再择时之后，可以使用 re_timing.py 进行再择时的资金曲线模拟
        # re_timing={'name': 'Bolling2', 'params': [334]}  # 可选，配置再择时策略
    ),

    dict(
        name='马嵬坡fast',
        strategy_list=[
            # ===========================================================
            # !!! 实盘前先回测一下，不要无脑跑案例策略，没人能保证案例策略能赚钱 !!!
            # 以下配置非官方提供的案例策略，这里只是举例如何配置，具体配置请自行处理
            # ===========================================================
            {
                "strategy": "马嵬坡fast",
                "offset_list": range(0, 24, 1),
                "hold_period": "24H",
                "is_use_spot": False,
                'cap_weight': 1,
                'long_cap_weight': 0,
                'short_cap_weight': 1,
                'long_select_coin_num': 0.1,
                'short_select_coin_num': 9999,
                "factor_list": [
                    ('QuoteVolumeMean修正', True, 168, 1),
                ],
                "filter_list": [
                    ('QuoteVolumeMean修正', x, 'pct:<0.2', False),
                    ('TopKTo2K', (x, f'{y}', z), 'val:==1'),
                    # ('新币落九天用的', 1, 'val:>5'),
                    ('Funding_fee_sum', 24, 'val:>=-0.0006'),
                ],
                "filter_list_post": [
                    ('浪Max23', 398, 'val:<0.5'),
                ],
                "use_custom_func": False,
            } for x in [168] for y in ['Cci过滤无现货删数据'] for z in [1000, 1800, 2600]
        ],
        # # 配置再择时之后，可以使用 re_timing.py 进行再择时的资金曲线模拟
        # re_timing={'name': 'Bolling2', 'params': [334]}  # 可选，配置再择时策略
    ),

    dict(
        name='回撤阈值空头',
        strategy_list=[
            {
                "strategy": "Strategy_截面因子",
                "offset_list": [4, 14],
                "hold_period": "24H",
                "market": "swap_swap",
                "cap_weight": 0.5 / 2,
                "long_cap_weight": 0,
                "short_cap_weight": 1,
                "long_select_coin_num": 0,
                "short_select_coin_num": 999,
                "factor_list": [
                    ('HoursSinceSpotAndSwap', True, 1, 1),
                ],
                "filter_list": [
                    ('HoursSinceSpotAndSwap', 1, 'val:>0'),
                    ('固定回撤阈值触发币数占比开仓门槛', (x, -0.20, 0.04), 'val:==1'),
                    ('CorrBTC', 60, 'pct:<0.3'),
                ],
                "use_custom_func": False,
                "short_filter_list_post": [
                    ('QpsSeqFundingSum', 336, 'val:>-0.5'),
                    ('QpsSeqDropHigh', 336, 'val:<0.85'),
                ],
            } for x in [39, 93]
        ] + [
            {
                "strategy": "Strategy_动态回撤阈值",
                "offset_list": [11, 13],
                "hold_period": "24H",
                "market": "swap_swap",
                "cap_weight": 0.5 / 4,
                "long_cap_weight": 0,
                "short_cap_weight": 1,
                "long_select_coin_num": 0,
                "short_select_coin_num": 999,
                "factor_list": [
                    ('HoursSinceSpotAndSwap', True, 1, 1),
                ],
                "filter_list": [
                    ('HoursSinceSpotAndSwap', 1, 'val:>0'),
                    ('动态回撤阈值', x, f'val:<{y}'),
                    ('CorrBTC', 60, 'pct:<0.3'),
                ],
                "use_custom_func": False,
                "short_filter_list_post": [
                    ('QpsSeqFundingSum', 336, 'val:>-0.5'),
                    ('QpsSeqDropHigh', 336, 'val:<0.85'),
                ],
            } for x in [53, 109] for y in [-3, -4]
        ],
    ),

    # 基线3新增 S13：资金费率负值区间空头。
    dict(
        name='Funding0021NegativeDomain',
        strategy_list=[
            dict(
                strategy='Funding0021NegativeDomain',
                offset_list=list(range(24)),
                hold_period='24H',
                market='swap_swap',
                cap_weight=0.25,
                long_cap_weight=0.0,
                short_cap_weight=1.0,
                long_select_coin_num=0,
                short_select_coin_num=0.65,
                long_factor_list=[],
                long_filter_list=[],
                short_factor_list=[
                    ('QpsSeqFundingSum', False, x, 1),
                ],
                short_filter_list=[
                    ('QpsSeqFundingSum', x, f'val:>{-0.010079999999999999 * x / 336:.12g}'),
                    ('QpsSeqFundingSum', x, f'val:<{z * x / 336:.12g}'),
                    ('ILLQStdBbi45208', y, 'pct:<=0.65', False),
                ],
                filter_list_post=[],
                use_custom_func=False,
                short_filter_list_post=[
                    ('QpsSeqFundingSum', 24, 'val:>-0.0006'),
                ],
            )
            for x, y, z in zip(
                [938, 1079, 938, 1079],
                [96, 96, 1104, 1104],
                [-0.00023999999999999998, -0.0003, -0.00023999999999999998, -0.0003],
                strict=True,
            )
        ],
    ),

    # 基线3新增 S14：四饼 + Bolling2(336)，保留推导式，不展开四个币的策略。
    dict(
        name='四饼',
        strategy_list=[
            {
                'strategy': f'Fourcake_{symbol}',
                'offset_list': [0],
                'hold_period': '8H',
                'market': market,
                'cap_weight': weight,
                'long_cap_weight': 1,
                'short_cap_weight': 0,
                'long_select_coin_num': 999,
                'short_select_coin_num': 0,
                'factor_list': [('SelectCoin', True, f'{symbol}-USDT', 1)],
                **({'long_filter_list': [('Funding_fee_sum', 168, 'val:<0.003', True)]}
                   if symbol == 'BNB' else {}),
                'use_custom_func': False,
            } for symbol, market, weight in zip(
                ['BTC', 'ETH', 'SOL', 'BNB'],
                ['spot_spot', 'spot_spot', 'spot_spot', 'swap_swap'],
                [0.2, 0.25, 0.2, 0.2],
            )
        ],
        re_timing={'name': 'Bolling2', 'params': [336]},
    ),
]
