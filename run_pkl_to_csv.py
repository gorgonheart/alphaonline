# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from compare_perf_commits import pkl_to_csv
from pathlib import Path
pkl_to_csv(Path('data\\30m_meiling0604\select_coin\\2026-06-23_23.pkl'))
pkl_to_csv(Path('data\\30m_meiling0604\select_coin\\2026-06-23_23线上.pkl'))