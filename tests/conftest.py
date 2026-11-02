"""Portable test inputs; paired producer is explicit and never started by tests."""
import os
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption('--producer-root', default=os.getenv('ALPHA_PRODUCER_ROOT'),
                     help='Paired alpha checkout with compiled extensions (read only)')


@pytest.fixture
def producer_root(request):
    value=request.config.getoption('--producer-root')
    root=Path(__file__).resolve().parents[1]
    choices=[Path(value)] if value else [root.parent/'alpha2',root.parent/'alpha']
    for path in choices:
        if (path/'core/arrow_publish.py').exists():
            return path
    pytest.skip('Paired producer source unavailable; set --producer-root for integration tests')


@pytest.fixture
def legacy_account_path():
    root=Path(__file__).resolve().parents[1]
    def locate(name):
        for path in (root/'accounts'/name,root/'tests/fixtures/legacy_accounts'/name):
            if path.exists():return path
        pytest.skip('Legacy public account fixture unavailable: '+name)
    return locate
