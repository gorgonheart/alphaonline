# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from compare_perf_commits import pkl_to_csv
from pathlib import Path
pkl_to_csv(Path('data\\10m_meiling0604\select_coin\\2026-06-24_01线上.pkl'))
pkl_to_csv(Path('data\\10m_meiling0604\select_coin\\2026-06-24_01.pkl'))