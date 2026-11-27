"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import sys
import traceback
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mtick
import pandas as pd

from config import error_webhook_url
from core.account_group import load_group
from core.utils.dingding import send_wechat_work_msg, send_wechat_work_img
from core.utils.path_kit import get_folder_path
from core.utils.statis_func import net_fund

# ====================================================================================================
# ** 账户组汇总统计 **
# 成员统计（statistics.py）已逐个发送并保存 data/<成员>/账户信息/equity.csv、transfer.csv，
# 这里只读取这些本地文件汇总，不调用交易所接口。
# - 时间对齐：各成员统计的实际时间相差几分钟，按 (time - hour_offset) 向下取整到小时归到同一批次，
#   批次时间取各成员中最晚的统计时间，保证这之前发生的出入金都计入本批次
# - 只保留所有成员都有记录的批次，不补零、不前向填充
# - 出入金：合并各成员的 transfer.csv；成员之间的划转一进一出，对组合净值基本抵消
# ====================================================================================================
SUM_COLS = ['账户总净值', '多头仓位', '空头仓位']
COUNT_COLS = ['多头选币', '空头选币']  # 持仓币数，同一个币可能在多个成员中都有持仓，只按成员分别画，不相加
GROUP_TRANSFER_MIN_RATIO = 0.001  # 组合曲线上小于组合权益 0.1% 的净出入金不标（成员互转的手续费差额、BF 利息等）
PLOT_DAYS = 30  # 汇总图只画最近 30 天，与单账户的 last 30 days 图一致
MEMBER_COLORS = ['#1f77b4', '#9467bd', '#17becf', '#e377c2', '#8c564b', '#bcbd22']

# 画图样式与成员统计（statistics.py）一致，组名等中文需要中文字体
plt.rcParams['font.sans-serif'] = ['Source Han Sans SC', 'SimHei', 'Arial Unicode MS', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
PCT_FORMATTER = mtick.FormatStrFormatter('%.2f%%')


def load_member_equity(member_id: str, hour_offset: pd.Timedelta) -> pd.DataFrame | None:
    """读取成员的权益记录，index 为批次，每个批次保留最后一条（含实际统计时间 time）"""
    path = get_folder_path('data', member_id, '账户信息', as_path_type=True) / 'equity.csv'
    if not path.exists():
        return None
    df = pd.read_csv(path, encoding='gbk')
    df['time'] = pd.to_datetime(df['time'], format='mixed')
    df['batch'] = (df['time'] - hour_offset).dt.floor('h')
    df['空头仓位'] = df['空头仓位'].abs()
    df = df.reindex(columns=['time', 'batch', *SUM_COLS, *COUNT_COLS])
    df[COUNT_COLS] = df[COUNT_COLS].fillna(0)
    return df.sort_values('time').groupby('batch')[['time', *SUM_COLS, *COUNT_COLS]].last()


def load_member_transfer(member_id: str) -> pd.DataFrame:
    """读取成员的出入金记录：time、账户总净值（金额，正为入金）、type=transfer"""
    path = get_folder_path('data', member_id, '账户信息', as_path_type=True) / 'transfer.csv'
    if not path.exists():  # 空表也带上列类型，否则和其他成员拼接后金额、时间会变成 object
        return pd.DataFrame({'time': pd.Series(dtype='datetime64[ns]'), '账户总净值': pd.Series(dtype=float),
                             'type': pd.Series(dtype=object)})
    df = pd.read_csv(path, encoding='gbk')
    df['time'] = pd.to_datetime(df['time'], format='mixed')
    return df


def calc_net(df_log: pd.DataFrame, df_transfer: pd.DataFrame) -> pd.DataFrame:
    """份额法净值：df_log 为权益记录（time、账户总净值 等），合并出入金后只返回 type=log 的行"""
    df_log = df_log.assign(type='log')
    df_transfer = df_transfer[df_transfer['time'] >= df_log['time'].min()]
    df = pd.concat([df_log, df_transfer] if not df_transfer.empty else [df_log], ignore_index=True)
    df = net_fund(df.sort_values('time', kind='stable').reset_index(drop=True))
    return df[df['type'] == 'log'].reset_index(drop=True)


def build_group_equity(group: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    """
    汇总组合权益和净值
    :return: (组合净值表（含 batch 列）, 各成员最新一批的权益, 各成员明细（index 为 batch，列为 (成员, 字段)，含成员净值）,
              各成员出入金 {成员: transfer.csv})
    """
    hour_offset = pd.Timedelta(minutes=int(group['hour_offset'][:-1]))
    member_dfs = {}
    for member_id in group['members']:
        df = load_member_equity(member_id, hour_offset)
        if df is None:
            raise FileNotFoundError(f'成员 {member_id} 还没有 equity.csv')
        member_dfs[member_id] = df

    # 只保留所有成员都有记录的批次
    df_all = pd.concat(member_dfs, axis=1, join='inner')
    if df_all.empty:
        raise ValueError('各成员没有共同的统计批次')
    df_group = pd.DataFrame({col: df_all.xs(col, axis=1, level=1).sum(axis=1) for col in SUM_COLS})  # 各成员求和
    df_group['time'] = df_all.xs('time', axis=1, level=1).max(axis=1)

    # 合并出入金，用份额法计算组合净值；成员之间的划转一进一出，对组合基本抵消
    transfers = {m: load_member_transfer(m) for m in group['members']}
    df = calc_net(df_group.reset_index(), pd.concat([t for t in transfers.values() if not t.empty] or [transfers[next(iter(transfers))]],
                                                    ignore_index=True))

    # 各成员净值：各自按自己的出入金计算（成员之间的划转对单个成员是出入金）
    for member_id in group['members']:
        df_member = df_all[member_id].reset_index()
        df_all[(member_id, '净值')] = calc_net(df_member, transfers[member_id])['净值'].to_numpy()

    df_members = df_all.iloc[-1].unstack()['账户总净值']
    return df, df_members, df_all, transfers


def locate_transfers(df_transfer: pd.DataFrame, times: pd.Series, net: pd.Series) -> pd.DataFrame:
    """
    出入金标记的位置：x 为出入金时间，y 为此前最近一个统计点的净值（出入金不改变净值），图上时间范围之外的丢掉
    :param df_transfer: time、账户总净值（金额，正为入金）
    :param times / net: 曲线的时间和净值（%），按时间升序
    """
    curve = pd.DataFrame({'time': times.to_numpy(), 'y': net.to_numpy()})
    df_transfer = df_transfer[(df_transfer['time'] <= curve['time'].iloc[-1])].sort_values('time')
    if df_transfer.empty:
        return df_transfer.assign(y=[])
    df_transfer = pd.merge_asof(df_transfer[['time', '账户总净值']], curve, on='time', direction='backward')
    return df_transfer.dropna(subset=['y'])


def draw_transfers(ax, points: pd.DataFrame, in_color='black', out_color='red', size=100):
    """与单账户一致：+ 为入金，x 为出金，不进图例"""
    adds, reduces = points[points['账户总净值'] > 0], points[points['账户总净值'] < 0]
    ax.scatter(adds['time'], adds['y'], marker='+', color=in_color, s=size, zorder=4)
    ax.scatter(reduces['time'], reduces['y'], marker='x', color=out_color, s=size, zorder=4)


def build_summary_msg(group: dict, df: pd.DataFrame, df_members: pd.Series) -> str:
    last = df.iloc[-1]
    equity = last['账户总净值']
    max_net = df['净值'].max()
    msg = f'【账户组汇总】{group["name"]}\n\n'
    msg += f'组合权益：{equity:,.2f} USDT\n'
    if len(df) > 1:
        msg += f'最近1小时盈亏：{equity - df.iloc[-2]["账户总净值"]:,.2f}\n'
    msg += f'组合净值：{last["净值"]:.4f}（距最高 {last["净值"] / max_net - 1:.2%}）\n'
    msg += f'多头仓位：{last["多头仓位"]:,.2f}（{last["多头仓位"] / equity:.2%}）\n'
    msg += f'空头仓位：{last["空头仓位"]:,.2f}（{last["空头仓位"] / equity:.2%}）\n\n'
    total = df_members.sum()
    for member_id, member_equity in df_members.items():
        msg += f'{member_id}：{member_equity:,.2f}，占比 {member_equity / total:.2%}（目标 {group["members"][member_id]:.2%}）\n'
    return msg


def draw_group_equity(group: dict, df: pd.DataFrame, df_all: pd.DataFrame, transfers: dict[str, pd.DataFrame],
                      pic_path: Path):
    """
    组合汇总图（最近 PLOT_DAYS 天，净值从窗口起点重新计算），颜色和画法与成员统计一致，自上而下：
    1. 组合净值 + 回撤，叠加各成员净值  2. 成员偏离目标（口径与划转提醒一致）+ 阈值线  3. 敞口  4. 多空仓位占比
    5. 各成员持仓币数  6. 只有组合的资金曲线 + 回撤
    子图 1、6 的组合曲线标注组合层面的净出入金，子图 1 的成员曲线标注成员各自的出入金
    """
    df = df[df['time'] >= df['time'].iloc[-1] - pd.Timedelta(days=PLOT_DAYS)].reset_index(drop=True)
    df['净值'] = df['净值'] / df['净值'].iloc[0]
    df['net'] = (df['净值'] - 1) * 100
    df['dd2here'] = (df['净值'] / df['净值'].cummax() - 1) * 100
    df['long_ratio'] = df['多头仓位'] / df['账户总净值']
    df['short_ratio'] = df['空头仓位'] / df['账户总净值']
    df['exposure'] = (df['long_ratio'] - df['short_ratio']) * 100
    # 组内成员杠杆可能不同，空仓按 1 倍杠杆计算；总仓位超过 1 倍时空仓为 0
    df['empty_ratio'] = (1 - df['long_ratio'] - df['short_ratio']).clip(lower=0)

    # 成员明细按批次对齐到组合的时间
    times = df.set_index('batch')['time']
    df_all = df_all.loc[times.index]
    equity_total = df_all.xs('账户总净值', axis=1, level=1).sum(axis=1)
    colors = {m: MEMBER_COLORS[i % len(MEMBER_COLORS)] for i, m in enumerate(group['members'])}

    fig, (ax1, ax_share, ax_exp, ax3, ax_cnt, ax_eq) = plt.subplots(
        6, 1, figsize=(12, 20), sharex=True, gridspec_kw={'height_ratios': [3, 1, 1, 1, 1, 3]})
    group_label = f'{group["name"].lstrip("_")}-Equity'  # 下划线开头的图例 matplotlib 不显示
    title = f'Group equity-curve(last {PLOT_DAYS} days) dd2here: {df.iloc[-1]["dd2here"]:.2f}% eq_max: {df["net"].max():.2f}%'

    # 组合层面的净出入金：同一小时内各成员相加（成员互转一进一出抵消），太小的不标
    group_transfer = pd.concat([t for t in transfers.values() if not t.empty] or [pd.DataFrame()], ignore_index=True)
    if not group_transfer.empty:
        group_transfer = (group_transfer.assign(hour=group_transfer['time'].dt.floor('h'))
                          .groupby('hour').agg(time=('time', 'last'), 账户总净值=('账户总净值', 'sum')).reset_index(drop=True))
        group_transfer = pd.merge_asof(group_transfer.sort_values('time'), df[['time', '账户总净值']].rename(columns={'账户总净值': '组合权益'}),
                                       on='time', direction='backward')
        group_transfer = group_transfer[group_transfer['账户总净值'].abs() > group_transfer['组合权益'] * GROUP_TRANSFER_MIN_RATIO]
    group_points = locate_transfers(group_transfer, df['time'], df['net']) if not group_transfer.empty else group_transfer

    def draw_equity(ax):
        """组合净值 + 右轴回撤 + 组合出入金标记"""
        ax.plot(df['time'], df['net'], color='#fb9300', label=group_label, linewidth=2, zorder=3)
        if not group_points.empty:
            draw_transfers(ax, group_points)
        ax.grid(True, which='both', linestyle='--', linewidth=0.5, zorder=1)
        ax.set_title(title)
        ax.yaxis.set_major_formatter(PCT_FORMATTER)
        ax_dd = ax.twinx()
        ax_dd.fill_between(df['time'], df['dd2here'], 0, color='#d5d5d5', alpha=0.2, zorder=0)
        ax_dd.set_ylabel('dd2here (%)')
        ax_dd.yaxis.set_major_formatter(PCT_FORMATTER)

    # 组合净值，叠加各成员净值（同样从窗口起点重新计算）
    draw_equity(ax1)
    for member_id, color in colors.items():
        share = df_all[(member_id, '账户总净值')].iloc[-1] / equity_total.iloc[-1]
        member_net = df_all[(member_id, '净值')] / df_all[(member_id, '净值')].iloc[0]
        ax1.plot(times, (member_net - 1) * 100, color=color, alpha=0.6, zorder=2, label=f'{member_id}-{share:.2%}')
        if not transfers[member_id].empty:  # 成员各自的出入金（含成员之间的互转），颜色与成员曲线一致
            draw_transfers(ax1, locate_transfers(transfers[member_id], times, (member_net - 1) * 100),
                           in_color=color, out_color=color, size=80)
    ax1.legend(loc='upper left')

    # 成员偏离目标：absolute 为 p - w（百分点），relative 为 (p - w) / w；虚线为划转提醒阈值
    relative = group['threshold_mode'] == 'relative'
    for member_id, color in colors.items():
        weight = group['members'][member_id]
        share = df_all[(member_id, '账户总净值')] / equity_total
        deviation = (share - weight) / (weight if relative else 1) * 100
        ax_share.plot(times, deviation, color=color, label=f'{member_id} {share.iloc[-1]:.2%}（目标 {weight:.2%}）')
    for sign in (1, -1):
        ax_share.axhline(sign * group['threshold'] * 100, color='gray', linestyle='--', linewidth=1)
    ax_share.axhline(0, color='gray', linewidth=0.5)
    ax_share.set_ylabel('相对偏离目标 (%)' if relative else '偏离目标 (百分点)')
    ax_share.yaxis.set_major_formatter(PCT_FORMATTER)
    ax_share.legend(loc='upper left')
    ax_share.grid(True, linestyle='--', linewidth=0.5)

    # 敞口 (多头 - 空头) / 组合权益
    ax_exp.fill_between(df['time'], df['exposure'].clip(lower=0), 0, color='green', alpha=0.7, label='Long Exposure')
    ax_exp.fill_between(df['time'], df['exposure'].clip(upper=0), 0, color='red', alpha=0.7, label='Short Exposure')
    ax_exp.axhline(0, color='gray', linewidth=0.5)
    ax_exp.set_ylabel('Exposure (%)')
    ax_exp.yaxis.set_major_formatter(PCT_FORMATTER)
    ax_exp.legend(loc='upper left')
    ax_exp.grid(True, linestyle='--', linewidth=0.5)

    # 多头、空头、空仓占比堆叠
    ax3.stackplot(df['time'], df['long_ratio'], df['short_ratio'], df['empty_ratio'],
                  labels=['Long', 'Short', 'empty'], colors=['#1eb100', '#ff634d', '#002e4d'], alpha=0.9)
    ax3.set_ylabel('Ratio')
    ax3.legend(loc='upper left')
    ax3.grid(True, linestyle='--', linewidth=0.5)

    # 各成员持仓币数（多头 + 空头），阶梯线，每个成员一条
    for member_id, color in colors.items():
        count = df_all[(member_id, '多头选币')] + df_all[(member_id, '空头选币')]
        ax_cnt.step(times, count, where='post', color=color, alpha=0.8, label=member_id)
    ax_cnt.set_ylabel('持仓币数')
    ax_cnt.legend(loc='upper left')
    ax_cnt.grid(True, linestyle='--', linewidth=0.5)

    # 只有组合的资金曲线，与单账户图底部的副图一致
    draw_equity(ax_eq)
    ax_eq.legend(loc='upper left')
    ax_eq.set_xlabel('Time')
    ax_eq.xaxis.set_tick_params(labelbottom=True)
    # 各子图共用 x 轴；按天数取间隔，30 天约 8 个刻度，避免日期标签重叠
    ax_eq.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, PLOT_DAYS // 8)))
    ax_eq.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))

    fig.tight_layout()
    fig.savefig(pic_path)
    plt.close(fig)


def run():
    run_time = datetime.fromtimestamp(int(sys.argv[1]))
    group = load_group(Path(sys.argv[2]))
    print(f'账户组汇总：{group["name"]}，{run_time}')

    df, df_members, df_all, transfers = build_group_equity(group)
    output_folder = get_folder_path('data', '账户组汇总', group['name'], as_path_type=True)
    df.drop(columns='batch').to_csv(output_folder / 'group_equity.csv', encoding='utf-8-sig', index=False)

    send_wechat_work_msg(build_summary_msg(group, df, df_members), group['webhook'])
    if len(df) > 1:
        pic_path = output_folder / 'group_equity.png'
        draw_group_equity(group, df, df_all, transfers, pic_path)
        send_wechat_work_img(str(pic_path), group['webhook'])


if __name__ == '__main__':
    try:
        run()
    except Exception as err:
        msg = f'账户组汇总统计出错，出错原因: {err}'
        print(msg)
        print(traceback.format_exc())
        send_wechat_work_msg(msg, error_webhook_url)
#󠀂󠁥󠀹󠁢󠁥󠀹󠁡󠁥󠀵󠀸󠁤󠀹󠁡󠁥󠀸󠀸󠀷󠁢󠀴󠀲󠀰󠀳󠀳󠀳󠀲󠀳󠀲󠀳󠀹󠀳󠀷󠁿
