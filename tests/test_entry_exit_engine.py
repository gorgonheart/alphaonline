"""entry_exit 选币引擎冒烟测试（2.2.1 新特性，无 legacy oracle，故以手工推演的期望持仓为准）。

覆盖 compute_entry_exit_numba 的核心状态机语义：逐截面 出场→留存→按 min-rank 补位到目标持仓数，
以及 exit_cooldown（出场拉黑期）、max_hold_hours（强制出场）、fill_lag（滞后补位）三个可选行为。
score 越小越优先（升序排名口径）。引擎纯计算、与数据路由无关（Arrow/pkl 均在其上游），
故不依赖真实数据，可无条件运行。

行顺序约定：每个 flat 期望数组按 [截面0 的各 symbol, 截面1 的各 symbol, ...]，
symbol 顺序 = 构造输入时的行顺序。"""
import numpy as np

from core.model.strategy_config import compute_entry_exit_numba


def _run(sections, n_symbols, fill_lag=0, exit_cooldown=0, max_hold_hours=0):
    """sections: list of per-section list[(symbol_id, score, in_mask, out_mask, target)].
    展开成 numba 引擎要的一维数组（times 用截面序号，同截面相邻）。"""
    times, sym, sc, inm, outm, tgt = [], [], [], [], [], []
    for t, rows in enumerate(sections):
        for sid, score, im, om, target in rows:
            times.append(t)
            sym.append(sid)
            sc.append(score)
            inm.append(im)
            outm.append(om)
            tgt.append(target)
    return compute_entry_exit_numba(
        np.array(times, dtype=np.int64),
        np.array(sym, dtype=np.int64),
        np.array(sc, dtype=np.float64),
        np.array(inm, dtype=np.int8),
        np.array(outm, dtype=np.int8),
        np.array(tgt, dtype=np.int64),
        n_symbols, int(fill_lag), int(exit_cooldown), int(max_hold_hours),
    )


def test_basic_fill_carry_and_exit_refill():
    """target=2；t0 补最优两只(A,B)；t1 持有不动(C 分更优但无空位)；t2 强制 A 出场→C 补位。"""
    # (id, score, in, out, target)  A=0 B=1 C=2
    sections = [
        [(0, 0.1, 1, 0, 2), (1, 0.2, 1, 0, 2), (2, 0.3, 1, 0, 2)],   # t0
        [(0, 0.5, 1, 0, 2), (1, 0.6, 1, 0, 2), (2, 0.05, 1, 0, 2)],  # t1
        [(0, 0.5, 1, 1, 2), (1, 0.6, 1, 0, 2), (2, 0.05, 1, 0, 2)],  # t2: A 强制出场
    ]
    got = _run(sections, n_symbols=3)
    expected = np.array([1, 1, 0,  1, 1, 0,  0, 1, 1], dtype=np.int32)
    np.testing.assert_array_equal(got, expected)


def test_exit_cooldown_blackout():
    """exit_cooldown=1：出场当期起拉黑 1 期，即便分数最优也不能立刻补回；冷却期满后可回。"""
    sections = [
        [(0, 0.1, 1, 0, 1), (1, 0.2, 1, 0, 1), (2, 0.3, 1, 0, 1)],  # t0: 补 A
        [(0, 0.1, 1, 1, 1), (1, 0.2, 1, 0, 1), (2, 0.3, 1, 0, 1)],  # t1: A 出场并拉黑→补 B
        [(0, 0.1, 1, 0, 1), (1, 0.2, 1, 1, 1), (2, 0.3, 1, 0, 1)],  # t2: B 出场→A 冷却已解→补 A
    ]
    got_cd = _run(sections, n_symbols=3, exit_cooldown=1)
    np.testing.assert_array_equal(got_cd, np.array([1, 0, 0,  0, 1, 0,  1, 0, 0], dtype=np.int32))

    # 对照：无冷却时 t1 A 出场后同期即以最优分补回，B 不会入选
    got_nocd = _run(sections, n_symbols=3, exit_cooldown=0)
    np.testing.assert_array_equal(got_nocd, np.array([1, 0, 0,  1, 0, 0,  1, 0, 0], dtype=np.int32))


def test_max_hold_hours_forced_exit():
    """max_hold_hours=2：单笔连续持有满 2 期后强制出场；配 exit_cooldown=1 让效果可见。"""
    # A 分恒优；B 次之。target=1。
    sections = [
        [(0, 0.1, 1, 0, 1), (1, 0.2, 1, 0, 1)],  # t0: 补 A, hold=1
        [(0, 0.1, 1, 0, 1), (1, 0.2, 1, 0, 1)],  # t1: A 留存 hold=2
        [(0, 0.1, 1, 0, 1), (1, 0.2, 1, 0, 1)],  # t2: A 满2期强制出场+拉黑→补 B
        [(0, 0.1, 1, 0, 1), (1, 0.2, 1, 0, 1)],  # t3: B 留存(A 冷却解但无空位)
    ]
    got = _run(sections, n_symbols=2, exit_cooldown=1, max_hold_hours=2)
    np.testing.assert_array_equal(got, np.array([1, 0,  1, 0,  0, 1,  0, 1], dtype=np.int32))

    # 对照：不限持有期时 A 一直留存
    got_nomax = _run(sections, n_symbols=2, exit_cooldown=1, max_hold_hours=0)
    np.testing.assert_array_equal(got_nomax, np.array([1, 0,  1, 0,  1, 0,  1, 0], dtype=np.int32))


def test_entry_gate_restricts_fill():
    """in_mask=0 的币不能补位，即便分数最优；target 补不满时留空。"""
    sections = [
        # A 分最优但入场门关闭(in=0)，B 入场门开→只补 B；C 也开但 target=1 已满
        [(0, 0.1, 0, 0, 1), (1, 0.2, 1, 0, 1), (2, 0.3, 1, 0, 1)],
    ]
    got = _run(sections, n_symbols=3)
    np.testing.assert_array_equal(got, np.array([0, 1, 0], dtype=np.int32))


def test_fill_lag_uses_prior_section_rank():
    """fill_lag=1：补位用上一期的分数与入场门（冷启动首期用当期）。"""
    # t0 冷启动按当期补最优 A。t1 补位看 t0 的分数：t0 时 B 分(0.9) 差、C 分(0.05) 最优，
    # 但 fill_lag 下 t1 的空位补位取 t0 排名 → 若 A 出场，用 t0 分数在非持仓里挑（C 的 t0 分 0.05 最优）。
    sections = [
        [(0, 0.1, 1, 0, 1), (1, 0.9, 1, 0, 1), (2, 0.05, 1, 0, 1)],  # t0: 当期补最优→C?
        [(0, 0.1, 1, 0, 1), (1, 0.9, 1, 0, 1), (2, 0.05, 1, 0, 1)],  # t1
    ]
    # 冷启动 t0：fill_lag>0 但 gi==0 → 用当期分数补最优。三只 in=1，最优 C(0.05)。→ C 持有。
    got = _run(sections, n_symbols=3, fill_lag=1)
    # t0: C 选中; t1: C 持有不动(out=0, target=1 已满)
    np.testing.assert_array_equal(got, np.array([0, 0, 1,  0, 0, 1], dtype=np.int32))
