from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence
from zipfile import ZIP_DEFLATED, ZipFile

from core.model.account_config import AccountConfig
from core.model.backtest_config import MultiEquityBacktestConfig
from core.utils.functions import del_hist_files
from core.utils.log_kit import logger
from core.utils.path_kit import get_folder_path


def _iter_source_files(source_path: Path) -> Iterable[Path]:
    for path in source_path.rglob('*'):
        if path.is_file():
            yield path


def _should_skip_cache_file(file_path: Path) -> bool:
    file_name = file_path.name
    if file_name in ('选币结果.pkl', 'equity_df.pkl', 'df_spot_ratio.pkl', 'df_swap_ratio.pkl', '最新持仓周期选币结果.pkl'):
        return True
    if file_name == 'all_candle_df_list.pkl':
        return True
    if file_name.endswith(('_full.pkl',)):
        return True
    if file_name.startswith('factor_full_') and file_name.endswith(('.pkl',)):
        return True
    return False


def _dedupe_source_paths(paths: Sequence[Path]) -> list[Path]:
    unique_paths: list[Path] = []

    for path in sorted({path for path in paths if path.exists()}, key=lambda item: (len(item.parts), str(item))):
        if any(path == saved_path or path.is_relative_to(saved_path) for saved_path in unique_paths):
            continue
        unique_paths.append(path)

    return unique_paths


def create_runtime_data_archive(
        account_name: str,
        run_time: datetime,
        data_root: Path | None = None,
        cache_path: Path | None = None,
        account_info_path: Path | None = None,
        backtest_paths: Sequence[Path] | None = None,
        max_file_limit: int = 12,
) -> Path | None:
    data_root = data_root or get_folder_path('data', as_path_type=True)
    cache_path = cache_path or (data_root / 'cache')
    account_info_path = account_info_path or (data_root / account_name / '账户信息')
    backtest_paths = backtest_paths or []

    archive_dir = data_root / account_name / 'runtime_data'
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f'{account_name}_{run_time.strftime("%Y%m%d_%H%M%S")}.zip'

    source_paths: list[Path] = []
    if cache_path.exists():
        source_paths.append(cache_path)
    if account_info_path.exists():
        source_paths.append(account_info_path)
    source_paths.extend(_dedupe_source_paths(backtest_paths))

    added_files = 0
    with ZipFile(archive_path, 'w', compression=ZIP_DEFLATED) as archive:
        for source_path in source_paths:
            for file_path in _iter_source_files(source_path):
                if _should_skip_cache_file(file_path):
                    continue

                try:
                    arcname = file_path.relative_to(data_root)
                except ValueError:
                    arcname = Path(source_path.name) / file_path.relative_to(source_path)

                archive.write(file_path, arcname=os.fspath(arcname))
                added_files += 1

    if added_files == 0:
        archive_path.unlink(missing_ok=True)
        return None

    del_hist_files(archive_dir, max_file_limit=max_file_limit, file_suffix='.zip')
    return archive_path


def save_runtime_data_archive(acct_conf: AccountConfig, me_conf: MultiEquityBacktestConfig, run_time):
    cache_path = get_folder_path('data', 'cache', as_path_type=True)
    account_info_path = get_folder_path('data', acct_conf.name, '账户信息', as_path_type=True)
    backtest_paths = [me_conf.factory.result_folder]

    for path_type, paths in (
            ('账户信息', [account_info_path]),
            ('子策略回测结果', backtest_paths),
    ):
        if not paths:
            logger.warning(f'[{acct_conf.name}] 未解析到 {path_type} 归集目录，跳过该数据源')
            continue

        for source_path in paths:
            if not source_path.exists():
                logger.warning(f'[{acct_conf.name}] {path_type} 归集目录不存在，跳过: {source_path}')

    archive_path = create_runtime_data_archive(
        account_name=acct_conf.name,
        run_time=run_time,
        cache_path=cache_path,
        account_info_path=account_info_path,
        backtest_paths=backtest_paths,
    )
    if archive_path is None:
        logger.warning(f'[{acct_conf.name}] 未找到可归集的实盘数据，跳过打包')
    else:
        logger.ok(f'[{acct_conf.name}] 实盘数据已打包至: {archive_path}')
#󠀂󠁥󠀹󠁢󠁥󠀹󠁡󠁥󠀵󠀸󠁤󠀹󠁡󠁥󠀸󠀸󠀷󠁢󠀴󠀲󠀰󠀳󠀳󠀳󠀲󠀳󠀲󠀳󠀹󠀳󠀷󠁿
