from pathlib import Path

import pytest

from file_organizer import FileOrganizer, MissingRequiredFieldsError

# TODO: More tests


def test_init():
    destination_dirs = {"Python"}
    signature_patterns = {"PY": "#!/.+?python"}
    extensions_map = {"PY": "Python"}

    organizer = FileOrganizer(destination_dirs, extensions_map)

    assert "Python" in organizer.dir_names
    assert ".PY" not in organizer.ext_to_dir
    assert organizer.ext_to_dir.get(".PY") is None
    assert organizer.ext_to_dir.get(".py") == "Python"

    assert organizer.signatures_re is None

    organizer = FileOrganizer(
        destination_dirs, extensions_map, signature_patterns
    )

    assert (
        organizer.signatures_re is not None
        and b"#!/.+?python" in organizer.signatures_re.pattern
    )


def test_from_ini(tmp_path):
    conf = tmp_path / "conf.ini"

    with pytest.raises(FileNotFoundError):
        FileOrganizer.from_ini(conf)

    ini = "[dir_names]\npython = Python"
    conf.write_text(ini)

    conf.chmod(0)
    with pytest.raises(PermissionError):
        FileOrganizer.from_ini(conf)

    conf.chmod(0o755)
    with pytest.raises(MissingRequiredFieldsError):
        FileOrganizer.from_ini(conf)

    ini += "\n[ext_to_dir]py = python"
    conf.write_text(ini)
    organizer = FileOrganizer.from_ini(conf)

    assert organizer.signatures_re is None

    conf = Path(__file__).with_name("extensions_map.ini")
    organizer = FileOrganizer.from_ini(conf)

    assert "Programming/Python" in organizer.dir_names
    assert (
        organizer.signatures_re is not None
        and b"#!/.+?python" in organizer.signatures_re.pattern
    )
    assert b"\x89PNG" in organizer.signatures_re.pattern
    assert "Programming/Python" == organizer.ext_to_dir[".py"]


def test_from_json(tmp_path):
    conf = tmp_path / "conf.json"

    with pytest.raises(FileNotFoundError):
        FileOrganizer.from_json(conf)

    conf.write_text("Some JSON")
    conf.chmod(0)

    with pytest.raises(PermissionError):
        FileOrganizer.from_json(conf)

    conf = Path(__file__).with_name("extensions_map.json")
    organizer = FileOrganizer.from_json(conf)

    assert "Programming/Python" in organizer.dir_names
    assert (
        organizer.signatures_re is not None
        and b"#!/.+?python" in organizer.signatures_re.pattern
    )
    assert "Programming/Python" in organizer.ext_to_dir[".py"]
