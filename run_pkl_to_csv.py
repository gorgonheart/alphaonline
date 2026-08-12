# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from compare_perf_commits import pkl_to_csv
from pathlib import Path
pkl_to_csv(Path('data/40m_dad0604_2.2.1/select_coin/2026-07-13_07线上.pkl'))
pkl_to_csv(Path('data/40m_dad0604_2.2.1/select_coin/2026-07-13_07.pkl'))