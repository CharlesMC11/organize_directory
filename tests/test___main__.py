import sys
from pathlib import Path
from unittest import mock

import pytest

from file_organizer.__main__ import main

CONF = r"""
[dir_names]
programming = Programming
python = Programming/Python

[ext_to_dir]
env = programming
py = python
"""


def test___main__(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["prog"])
    with pytest.raises(SystemExit):
        main()
    assert "the following arguments are required:" in capsys.readouterr().err

    conf = tmp_path / "config.ini"
    conf.write_text(CONF)

    test_dir = tmp_path / "test_dir"
    test_dir.mkdir()

    test_file = test_dir / "test_file.py"
    test_file.write_text("Hello World")

    monkeypatch.setattr(sys, "argv", ("prog", str(test_dir), str(conf)))
    main()
    assert "" in capsys.readouterr().err
    assert not test_file.exists()
    assert test_dir.joinpath("Programming", "Python", test_file.name).exists()
