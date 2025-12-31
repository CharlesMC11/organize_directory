from pathlib import Path

import pytest

from file_organizer import FileOrganizer, MissingRequiredFieldsError


def test_init():
    destination_dirs = {"Python"}
    signature_patterns = {"PY": "#!/.+?python"}
    extensions_map = {"PY": "Python"}

    organizer = FileOrganizer(destination_dirs, extensions_map)

    assert "Python" in organizer.destination_dirs
    assert "PY" not in organizer.extensions_map
    assert organizer.extensions_map.get("PY") is None
    assert organizer.extensions_map.get("py") == "Python"

    assert organizer.signature_patterns is None

    organizer = FileOrganizer(
        destination_dirs, extensions_map, signature_patterns
    )

    assert b"#!/.+?python" in organizer.signature_patterns.pattern


def test_from_ini(tmp_path):
    conf = tmp_path / "conf.ini"

    with pytest.raises(FileNotFoundError, match="No such file:"):
        FileOrganizer.from_ini(conf)

    ini = "[destination_dirs]\npython = Python"
    conf.write_text(ini)

    conf.chmod(0)
    with pytest.raises(PermissionError, match="Permission denied:"):
        FileOrganizer.from_ini(conf)

    conf.chmod(0o755)
    with pytest.raises(MissingRequiredFieldsError, match="Missing required sections:"):
        FileOrganizer.from_ini(conf)

    ini += "\n[extensions_map]py = python"
    conf.write_text(ini)
    organizer = FileOrganizer.from_ini(conf)

    assert organizer.signature_patterns is None

    conf = Path(__file__).with_name("extensions_map.ini")
    organizer = FileOrganizer.from_ini(conf)

    assert "Programming/Python" in organizer.destination_dirs
    assert b"#!/.+?python" in organizer.signature_patterns.pattern
    assert "Programming/Python" == organizer.extensions_map["py"]


def test_from_json(tmp_path):
    conf = tmp_path / "conf.json"

    with pytest.raises(FileNotFoundError, match="No such file:"):
        FileOrganizer.from_json(conf)

    conf.write_text("Some JSON")
    conf.chmod(0)

    with pytest.raises(PermissionError, match="Permission denied:"):
        FileOrganizer.from_json(conf)

    conf = Path(__file__).with_name("extensions_map.json")
    organizer = FileOrganizer.from_json(conf)

    assert "Programming/Python" in organizer.destination_dirs
    assert b"#!/.+?python" in organizer.signature_patterns.pattern
    assert "Programming/Python" in organizer.extensions_map["py"]
