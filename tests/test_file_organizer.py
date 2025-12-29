import shutil
from zipfile import ZipFile

import pytest

from file_organizer import FileOrganizer

TEST_CONFIG = """
[destination_dirs]
archives = Archives
images = Images
images_raw = Images/Raw
programming = Programming
python = Programming/Python
shell = Programming/Shell

[signature_patterns]
py = #!/.+?python
sh = #!/.+?sh
zip = PK\x03\x04

[extensions_map]
jpeg = images
dng = images_raw
py = python
sh = shell
zip = archives
"""


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


@pytest.fixture
def organizer(tmp_path):
    conf = tmp_path / "conf.cfg"
    conf.write_text(TEST_CONFIG)

    return FileOrganizer.from_ini(conf)


def test_extensionless(organizer, tmp_path):
    python = tmp_path / "python"
    python.write_text('#!/usr/bin/env -S python3\n\nprint("Hello, World!")\n')
    python_target = organizer.get_extensionless_dst(python)

    bash = tmp_path / "bash"
    bash.write_text("#!/usr/bin/env -S bash\n\necho 'Hello, World!'\n")
    bash_target = organizer.get_extensionless_dst(bash)

    zipfile = tmp_path / "zip"
    with ZipFile(zipfile, mode="x") as f:
        f.write(python)
    zipfile_target = organizer.get_extensionless_dst(zipfile)

    unknown = tmp_path / "unknown"
    unknown.write_text("Some unknown file")
    unknown_target = organizer.get_extensionless_dst(unknown)

    assert python_target == organizer.extensions_map["py"]
    assert bash_target == organizer.extensions_map["sh"]
    assert zipfile_target == organizer.extensions_map["zip"]
    assert unknown_target == organizer.MISC_DIR


def test_move_file_and_sidecar(organizer, tmp_path):
    organizer._create_destination_dirs(tmp_path)

    img = tmp_path / "jpeg.jpeg"
    img.write_text("Some image")

    xmp = tmp_path / "jpeg.xmp"
    xmp.write_text("Some sidecar")

    raw = tmp_path / "raw.dng"
    raw.write_text("Some image raw data")

    xmp2 = tmp_path / "raw.xmp"
    xmp2.write_text("Some sidecar")

    xmp3 = tmp_path / "xmp.xmp"
    xmp3.write_text("Some dangling xmp file")

    img_target = tmp_path / organizer.extensions_map.get(
        img.suffix.lstrip("."), organizer.MISC_DIR
    )
    raw_target = tmp_path / organizer.extensions_map.get(
        raw.suffix.lstrip("."), organizer.MISC_DIR
    )
    xmp_target = tmp_path / organizer.extensions_map.get(
        xmp3.suffix.lstrip("."), organizer.MISC_DIR
    )

    assert img_target == tmp_path / "Images"
    assert raw_target == tmp_path / "Images/Raw"

    organizer.move_file_and_sidecar(img, img_target / img.name)
    organizer.move_file_and_sidecar(raw, raw_target / raw.name)

    assert (img_target / "jpeg.jpeg").exists()
    assert (img_target / "jpeg.xmp").exists()

    assert (raw_target / "raw.dng").exists()
    assert (raw_target / "raw.xmp").exists()

    assert not img.exists()
    assert not raw.exists()

    for f in xmp, xmp2, xmp3:
        try:
            shutil.move(f, xmp_target)

        except FileNotFoundError:
            assert not f.exists()

        else:
            assert (xmp_target / "xmp.xmp").exists()


def test__get_unique_destination_path(organizer, tmp_path):
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    dst = dst_dir / "file.txt"
    dst.write_text("Hello, World!")

    new_path = organizer._get_unique_destination_path(dst)

    assert new_path == dst.with_stem(dst.stem + "_1")
