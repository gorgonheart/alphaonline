"""The production hot path does not import experiment profiling wrappers."""
from pathlib import Path


def test_no_temporary_profiling_in_runtime():
    root = Path(__file__).resolve().parents[1]
    files = [root/'startup.py', *(root/'core').rglob('*.py'),
             *(root/'tools/robust40').rglob('*.py')]
    forbidden = ('ACCOUNT_TIMING', 'EXECUTION_PHASE', 'FACTOR_BLOCK_PROFILE', 'CHILD_SIMULATION',
                 'from core.utils.factor_metrics import')
    found = [(str(p.relative_to(root)), token) for p in files
             for token in forbidden if token in p.read_text(encoding='utf8')]
    assert not found, found
