"""Window capture stays bounded and cleans up temporary screenshots."""
import subprocess
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from thumb import mirror


@pytest.mark.parametrize('outcome', ['success', 'timeout', 'failure'])
def test_cli_capture_bounds_wait_and_removes_temp_file(monkeypatch, outcome):
    paths = []

    def capture(command, **kwargs):
        assert command[:4] == ['screencapture', '-x', '-o', '-l123']
        assert kwargs['timeout'] == 10
        path = Path(command[-1])
        paths.append(path)
        if outcome == 'timeout':
            raise subprocess.TimeoutExpired(command, 10)
        if outcome == 'failure':
            return SimpleNamespace(returncode=1)
        Image.new('RGBA', (40, 80)).save(path)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(mirror.subprocess, 'run', capture)
    result = mirror._capture_screencapture_cli(123)
    assert all(not path.exists() for path in paths)
    if outcome == 'success':
        assert result.size == (40, 80)
    else:
        assert result is None
