import os.path
from zipfile import ZipFile

import pytest

from file_organizer import FileOrganizer

TEST_CONFIG = r"""
[destination_dirs]
archives = Archives
images = Images
images_raw = Images/Raw
programming = Programming
python = Programming/Python
shell = Programming/Shell

[signature_patterns]
png = \x89PNG
py = #!/.+?python
sh = #!/.+?sh
zip = PK\x03\x04

[extensions_map]
jpeg = images
png = images
dng = images_raw
cfg = programming
py = python
sh = shell
zip = archives
"""


@pytest.fixture
def organizer(tmp_path):
    conf = tmp_path / "conf.cfg"
    conf.write_text(TEST_CONFIG)

    return FileOrganizer.from_ini(conf)


def test__determine_dst(organizer, tmp_path):
    organizer._create_destination_dirs(tmp_path)

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
        d.name: organizer.FALLBACK_DIR_NAME,
        f.name: None,
        x.name: "DEFER",
        e.name: organizer.extension_to_dir[".py"],
        p.name: organizer.extension_to_dir[".py"],
        r.name: organizer.extension_to_dir[".dng"],
        **{k: None for k in organizer.destination_dir_names},
        "conf.cfg": organizer.extension_to_dir[".cfg"],
    }

    for entry in tmp_path.iterdir():
        dst = organizer._determine_dst(entry)
        result = paths[entry.name]

        assert result is dst if not result else result == dst


def test__get_extensionless_dst(organizer, tmp_path):
    png = tmp_path / "png"
    png.write_bytes(b"\x89PNG")
    png_target = organizer._get_extensionless_dst(png)

    python = tmp_path / "python"
    python.write_text('#!/usr/bin/env -S python3\n\nprint("Hello, World!")\n')
    python_target = organizer._get_extensionless_dst(python)

    bash = tmp_path / "bash"
    bash.write_text("#!/usr/bin/env -S bash\n\necho 'Hello, World!'\n")
    bash_target = organizer._get_extensionless_dst(bash)

    zipfile = tmp_path / "zip"
    with ZipFile(zipfile, mode="x") as f:
        f.write(python)
    zipfile_target = organizer._get_extensionless_dst(zipfile)

    unknown = tmp_path / "unknown"
    unknown.write_text("Some unknown file")
    unknown_target = organizer._get_extensionless_dst(unknown)

    assert png_target == organizer.extension_to_dir[".png"]
    assert python_target == organizer.extension_to_dir[".py"]
    assert bash_target == organizer.extension_to_dir[".sh"]
    assert zipfile_target == organizer.extension_to_dir[".zip"]
    assert unknown_target == organizer.FALLBACK_DIR_NAME


def test__move_file_and_sidecar(organizer, tmp_path):
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

    img_target = tmp_path / organizer.extension_to_dir.get(
        img.suffix, organizer.FALLBACK_DIR_NAME
    )
    raw_target = tmp_path / organizer.extension_to_dir.get(
        raw.suffix, organizer.FALLBACK_DIR_NAME
    )
    xmp_target = tmp_path / organizer.extension_to_dir.get(
        xmp3.suffix, organizer.FALLBACK_DIR_NAME
    )

    assert img_target == tmp_path / "Images"
    assert raw_target == tmp_path / "Images/Raw"

    organizer._move_file_and_sidecar(img, img_target)
    organizer._move_file_and_sidecar(raw, raw_target)

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


def test__try_move(organizer, tmp_path):
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir(mode=0o000)

    f = tmp_path / "file.txt"
    f.write_text("Hello, World!")

    result = organizer._try_move_into(f, dst_dir)
    assert result is None

    dst_dir.chmod(0o755)
    result = organizer._try_move_into(f, dst_dir)
    assert result == dst_dir / f.name


def test__generate_unique_destination_path(organizer, tmp_path):
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    dst = dst_dir / "file.txt"
    dst.write_text("Hello, World!")

    new_path = next(organizer._generate_unique_destination_path(dst))
    padding = len(str(organizer.max_collision_attempts))

    assert new_path.stem == f"file_{1:0{padding}}"
