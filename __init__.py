__author__ = "Charles Mesa Cayobit"

import re
from configparser import ConfigParser
from pathlib import Path

TARGETS_FILE = Path(__file__).with_name("targets.cfg")

SHEBANG_PY = re.compile(r"#!.*?python")
SHEBANG_SH = re.compile(r"#!.*?sh")

MISC_DIR = "Misc"


class FileOrganizer:

    def __init__(self, targets_file: Path) -> None:
        parser = ConfigParser()
        parser.read(targets_file)

        directories = set(parser["directories"].values())
        directories.add(MISC_DIR)

        targets = {
            file_extension: parser["directories"][target_path]
            for file_extension, target_path in parser["targets"].items()
        }

        self._directories = directories
        self._targets = targets

    @property
    def directories(self) -> set[str]:
        return self._directories

    @property
    def targets(self) -> dict[str, str]:
        return self._targets


ORGANIZER = FileOrganizer(TARGETS_FILE)


def move_file(file: Path, target_dir: Path) -> None:
    """Move `file` into `target_dir`."""

    file.rename(target_dir / file.name)


def move_extensionless(file: Path, root_dir: Path) -> None:
    """Move a file without an extension."""

    target_dir = MISC_DIR
    try:
        with file.open() as f:
            header = f.readline()

    except (IOError, UnicodeDecodeError):
        pass  # Do nothing because the target defaults to `MISC_DIR`

    else:
        if SHEBANG_PY.match(header):
            target_dir = ORGANIZER.targets["py"]

        elif SHEBANG_SH.match(header):
            target_dir = ORGANIZER.targets["sh"]

    move_file(file, root_dir / target_dir)


def move_image(image_file: Path, target_dir: Path) -> None:
    """Move an image and its sidecar file to `target_dir`."""

    move_file(image_file, target_dir)

    sidecar_file = image_file.with_suffix(".xmp")
    try:
        move_file(sidecar_file, target_dir)
    except FileNotFoundError:
        pass  # Do nothing if a sidecar file does not exist.


# `move_image()` will move an image's existing sidecar file alongside the
# image, so defer processing XMP files to the end.
xmp_files: list[Path] = []


def move(file: Path, root_dir: Path) -> None:
    """Move the file

    :param file: The file to move
    :param root_dir: The root directory to move to
    """

    if file.name in ORGANIZER.directories or file.name == ".DS_Store":
        return

    elif file.is_dir():
        move_file(file, root_dir / MISC_DIR)
        return

    file_ext = file.suffix
    if not file_ext:
        move_extensionless(file, root_dir)
        return

    file_ext = file_ext[1:].lower()
    if file_ext == "xmp":
        global xmp_files
        xmp_files.append(file)
        return

    target_dir = ORGANIZER.targets.get(file_ext, MISC_DIR)
    if target_dir == ORGANIZER.targets["jpg"] or target_dir == ORGANIZER.targets["dng"]:
        move_image(file, root_dir / target_dir)

    else:
        move_file(file, root_dir / target_dir)


def main(root_dir: Path) -> None:
    for directory in ORGANIZER.directories:
        (root_dir / directory).mkdir(parents=True, exist_ok=True)

    for file in root_dir.iterdir():
        move(file, root_dir)

    for xmp_file in xmp_files:
        try:
            move_file(xmp_file, root_dir / MISC_DIR)
        except FileNotFoundError:
            pass  # Do nothing if the image sidecar file had already been moved.
