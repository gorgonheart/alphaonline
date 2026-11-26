"""
账户组配置：本目录 = 一个账户组，目录名即组名
- 成员文件放在本目录，格式与单账户完全一致；成员文件名（不含 .py）全局唯一
- 组内成员的 hour_offset 必须一致，不一致启动时报错
- 规则见 docs/实盘账户组设计.md

每轮流程：
1. 计算轮：各成员选币、仓管，算出最终权重和账户权益 E_i，不下单
2. 组合层：账户层动态杠杆 k_i = min(ΣE × w_i / E_i, max_scale)
3. 下单轮：各成员按 自身权益 × 杠杆 × k_i 下单（k > 1 时现货部分不放大）
"""
# 成员文件名(不含 .py): 目标资金比例，合计为 1
# 必须是当前账户组目录下的 py 文件名
members = {
    'demo_a': 0.5,
    'demo_b': 0.5,
}

# 任一成员资金占比偏离目标超过阈值，每轮发送划转提醒（实盘不自动划转，需要手动处理）
# 阈值口径：'absolute' 偏离的百分点（默认）/ 'relative' 偏离占目标比例的比例
# 例：OKX 目标 5%，absolute 0.05 要跌到 0% 才触发；relative 0.2 在低于 4% 或高于 6% 时触发
rebalance_threshold_mode = 'absolute'
rebalance_threshold = 0.05

# 单边账户层动态杠杆 k 的上限，不允许超过 2
max_scale = 2

# 组汇总机器人；不填时使用 config.py 的 error_webhook_url
wechat_webhook_url = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key='

# 划转提醒、整组跳过、下单轮出错通知的机器人，可不填；不填时使用 config.py 的 error_webhook_url
transfer_webhook_url = ''
#󠀂󠁥󠀹󠁢󠁥󠀹󠁡󠁥󠀵󠀸󠁤󠀹󠁡󠁥󠀸󠀸󠀷󠁢󠀴󠀲󠀰󠀳󠀳󠀳󠀲󠀳󠀲󠀳󠀹󠀳󠀷󠁿
