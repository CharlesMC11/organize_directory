import pytest

from file_organizer import FileOrganizer


def test_init():
    destination_dirs = {"Python"}
    signature_patterns = {"PY": "#!/.+?python"}
    extensions_map = {"PY": "Python"}

    organizer = FileOrganizer(
        destination_dirs, signature_patterns, extensions_map
    )

    assert "Python" in organizer.destination_dirs
    assert b"#!/.+?python" in organizer.signature_patterns.pattern

    assert "PY" not in organizer.extensions_map
    assert "Python" in organizer.extensions_map["py"]


def test_from_ini(tmp_path):
    conf = tmp_path / "conf.ini"

    with pytest.raises(FileNotFoundError):
        FileOrganizer.from_ini(conf)

    ini = """
[destination_dirs]
python = Python
"""
    conf.write_text(ini)

    with pytest.raises(ValueError):
        FileOrganizer.from_ini(conf)

    ini += """
[signature_patterns]
py = #!/.+?python

[extensions_map]
py = python
"""

    conf.write_text(ini)

    organizer = FileOrganizer.from_ini(conf)

    assert "Python" in organizer.destination_dirs
    assert b"#!/.+?python" in organizer.signature_patterns.pattern
    assert "Python" == organizer.extensions_map["py"]


def test_from_json(tmp_path):
    conf = tmp_path / "conf.json"

    with pytest.raises(FileNotFoundError):
        FileOrganizer.from_json(conf)

    json = """
{
    "destination_dirs": {
        "programming": "Programming",
        "python": "Programming/Python"
    },
    "signature_patterns": {
        "py": "#!/.+?python"
    },
    "extensions_map": {
        "python": ["py", "pyc", "pyi"]
    }
}
"""

    conf.write_text(json)
    organizer = FileOrganizer.from_json(conf)

    assert "Programming/Python" in organizer.destination_dirs
    assert b"#!/.+?python" in organizer.signature_patterns.pattern
    assert "Programming/Python" in organizer.extensions_map["py"]
