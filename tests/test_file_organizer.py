import logging
import os
from pathlib import Path
from zipfile import ZipFile

import pytest

import file_organizer
from file_organizer import FileOrganizer, LogActions, OrganizerConfig

# TODO: More tests

logger = logging.getLogger(file_organizer.__name__)
logger.setLevel(logging.DEBUG)


@pytest.fixture
def organizer():
    conf = OrganizerConfig(
        (
            "Archives",
            "Images",
            "Images/Raw",
            "Misc",
            "Programming",
            "Programming/Python",
            "Programming/Shell",
        ),
        {
            ".zip": "Archives",
            ".jpeg": "Images",
            ".png": "Images",
            ".dng": "Images/Raw",
            ".cfg": "Programming",
            ".env": "Programming",
            ".py": "Programming/Python",
            ".sh": "Programming/Shell",
        },
        {
            ".zip": r"PK\x03\x04",
            ".py": r"#!.*?python",
            ".png": r"\x89PNG",
            ".sh": r"#!.*?sh",
        },
    )

    return FileOrganizer(conf)


def test__create_dirs(organizer, tmp_path):
    test_dir = tmp_path / "test_dir"

    assert not (test_dir / organizer.config.DEFAULT_DIR_NAME).is_dir()

    test_dir.mkdir(0)
    assert test_dir.is_dir()

    with pytest.raises(PermissionError):
        organizer._create_dirs(test_dir)

    organizer._create_dirs(tmp_path)
    assert (tmp_path / organizer.config.DEFAULT_DIR_NAME).is_dir()


def test__get_dst_dir_name(organizer, tmp_path):
    organizer._create_dirs(tmp_path)

    s = tmp_path / "s"
    s.symlink_to(os.path.expanduser("~/Desktop/untitled"))

    d = tmp_path / "d"
    d.mkdir()

    f = tmp_path / "f"
    os.mkfifo(f)

    x = tmp_path / "x.xmp"
    x.write_text("Some xmp")

    e = tmp_path / "e"
    e.write_text('#!/usr/bin/env python\nprint("Hello, World!")\n')

    p = tmp_path / "p.py"
    p.write_text("#!/usr/bin/env python\nprint('Hello, World!')\n")

    r = tmp_path / "h.dng"
    r.write_bytes(b"Some raw photo")

    paths = {
        s.name: None,
        d.name: organizer.config.DEFAULT_DIR_NAME,
        f.name: None,
        x.name: None,
        e.name: organizer.config.ext_to_dir[".py"],
        p.name: organizer.config.ext_to_dir[".py"],
        r.name: organizer.config.ext_to_dir[".dng"],
        **{k: None for k in organizer.config.dir_names},
        "conf.cfg": organizer.config.ext_to_dir[".cfg"],
    }

    for entry in tmp_path.iterdir():
        dst = organizer._get_dst_dir_name(entry)
        result = paths[entry.name]

        assert result is dst if not result else result == dst


def test__get_dst_dir_name_by_signature(organizer, caplog, tmp_path):
    png = tmp_path / "png"
    png.write_bytes(b"\x89PNG")
    png_target = organizer._get_dst_dir_name_by_signature(png)

    python = tmp_path / "python"
    python.write_text('#!/usr/bin/env -S python3\n\nprint("Hello, World!")\n')
    python_target = organizer._get_dst_dir_name_by_signature(python)

    bash = tmp_path / "bash"
    bash.write_text("#!/usr/bin/env -S bash\n\necho 'Hello, World!'\n")
    bash_target = organizer._get_dst_dir_name_by_signature(bash)

    zipfile = tmp_path / "zip"
    with ZipFile(zipfile, mode="x") as f:
        f.write(python)
    zipfile_target = organizer._get_dst_dir_name_by_signature(zipfile)

    unknown = tmp_path / "unknown"
    unknown.write_text("Some unknown file")
    unknown_target = organizer._get_dst_dir_name_by_signature(unknown)

    assert png_target == organizer.config.ext_to_dir[".png"]
    assert python_target == organizer.config.ext_to_dir[".py"]
    assert bash_target == organizer.config.ext_to_dir[".sh"]
    assert zipfile_target == organizer.config.ext_to_dir[".zip"]
    assert unknown_target == organizer.config.DEFAULT_DIR_NAME

    args = ("Images",), {" ..PNG  ": "Images"}
    conf = OrganizerConfig(*args)
    fo = FileOrganizer(conf)
    assert conf.signatures_re is None
    assert fo._get_dst_dir_name_by_signature(png) == conf.DEFAULT_DIR_NAME
    # assert f"{LogActions.SKIPPED}: No regex" in caplog.text

    png.chmod(0)
    assert fo._get_dst_dir_name_by_signature(png) == conf.DEFAULT_DIR_NAME
    # assert f"{LogActions.FAILED}: Opening" in caplog.text

    args = *args, {" ....Png   ": r"\x89PNG"}
    conf = OrganizerConfig(*args)
    fo = FileOrganizer(conf)
    assert fo._get_dst_dir_name_by_signature(python) == conf.DEFAULT_DIR_NAME


def test__move_file_and_sidecar(organizer, tmp_path):
    organizer._create_dirs(tmp_path)

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

    img_target = tmp_path / organizer.config.ext_to_dir.get(
        img.suffix, organizer.config.DEFAULT_DIR_NAME
    )
    raw_target = tmp_path / organizer.config.ext_to_dir.get(
        raw.suffix, organizer.config.DEFAULT_DIR_NAME
    )
    xmp_target = tmp_path / organizer.config.ext_to_dir.get(
        xmp3.suffix, organizer.config.DEFAULT_DIR_NAME
    )

    assert img_target == tmp_path / "Images"
    assert raw_target == tmp_path / "Images/Raw"

    organizer._move_file_and_sidecar(img, img_target / img.name)
    organizer._move_file_and_sidecar(raw, raw_target / raw.name)

    assert (img_target / "jpeg.jpeg").exists()
    assert (img_target / "jpeg.xmp").exists()

    assert (raw_target / "raw.dng").exists()
    assert (raw_target / "raw.xmp").exists()

    assert not img.exists()
    assert not raw.exists()

    for f in xmp, xmp2, xmp3:
        try:
            f.move_into(xmp_target)

        except FileNotFoundError:
            assert not f.exists()

        else:
            assert (xmp_target / "xmp.xmp").exists()


def test__move(organizer, caplog, tmp_path):
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir(0)

    f1 = tmp_path / "file.txt"
    f2 = Path(f1)

    f1.write_text("Hello, World!")

    dst_dir.chmod(0o755)
    result = organizer._move(f1, dst_dir / f1.name)
    assert result == dst_dir / f1.name

    f2.write_text("Hello, World!")
    result = organizer._move(f2, dst_dir / f2.name)
    assert result == dst_dir / f"{f2.stem}_01{f2.suffix}"


def test__generate_unique_destination_path(organizer, tmp_path):
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    dst = dst_dir / "file.txt"
    dst.write_text("Hello, World!")

    new_path = next(organizer._generate_unique_destination_path(dst))
    padding = len(str(organizer.config.max_collision_attempts))
    assert new_path.stem == f"file_{1:0{padding}}"
