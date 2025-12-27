import shutil
from zipfile import ZipFile

import pytest

from file_organizer import FileOrganizer

TEST_CONFIG = """
[subdirectories]
archives = Archives
images = Images
images_raw = Images/Raw
programming = Programming
python = Programming/Python
shell = Programming/Shell

[header_patterns]
py = #!/.+?python
sh = #!/.+?sh
zip = PK\x03\x04

[targets]
jpeg = images
dng = images_raw
py = python
sh = shell
zip = archives
"""


@pytest.fixture
def organizer(tmp_path):
    conf = tmp_path / "conf.cfg"
    conf.write_text(TEST_CONFIG)

    return FileOrganizer(conf)


def test_extensionless(organizer, tmp_path):
    python = tmp_path / "python"
    python.write_text('#!/usr/bin/env -S python3\n\nprint("Hello, World!")\n')
    python_target = organizer.get_extensionless_target(python)

    bash = tmp_path / "bash"
    bash.write_text("#!/usr/bin/env -S bash\n\necho 'Hello, World!'\n")
    bash_target = organizer.get_extensionless_target(bash)

    zipfile = tmp_path / "zip"
    with ZipFile(zipfile, mode="x") as f:
        f.write(python)
    zipfile_target = organizer.get_extensionless_target(zipfile)

    assert python_target == organizer.targets["py"]
    assert bash_target == organizer.targets["sh"]
    assert zipfile_target == organizer.targets["zip"]


def test_misc_fallback(organizer, tmp_path):
    f = tmp_path / "unknown"
    f.write_text("Some unknown file")

    target = organizer.get_extensionless_target(f)

    assert target == organizer.MISC_DIR


def test_sidecar(organizer, tmp_path):
    organizer._create_subdirectories(tmp_path)

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

    img_target = tmp_path / organizer.targets.get(
        img.suffix.lstrip("."), organizer.MISC_DIR
    )
    raw_target = tmp_path / organizer.targets.get(
        raw.suffix.lstrip("."), organizer.MISC_DIR
    )
    xmp_target = tmp_path / organizer.targets.get(
        xmp3.suffix.lstrip("."), organizer.MISC_DIR
    )

    assert img_target == tmp_path / "Images"
    assert raw_target == tmp_path / "Images/Raw"

    organizer.move_file(img, img_target)
    organizer.move_file(raw, raw_target)

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
