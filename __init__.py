__author__ = "Charles Mesa Cayobit"

import re
import shutil
from configparser import ConfigParser
from pathlib import Path

TARGETS_FILE = Path(__file__).with_name("targets.cfg")


class FileOrganizer:
    HEADERS = ((re.compile(rb"#!.*?python"), "py"), (re.compile(rb"#!.*?sh"), "sh"))

    MISC_DIR = "Misc"

    def __init__(self, targets_file: Path) -> None:
        parser = ConfigParser()
        parser.read(targets_file)

        directories = set(parser["directories"].values())
        directories.add(self.MISC_DIR)

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

    def get_extensionless_target(self, file: Path) -> str:
        target_dir = self.MISC_DIR
        try:
            with file.open("rb") as f:
                header = f.read(1024)

        except (IOError, UnicodeDecodeError):
            pass  # Do nothing because the target defaults to `MISC_DIR`

        else:
            for pattern, key in self.HEADERS:
                if pattern.match(header):
                    return self.targets.get(key, self.MISC_DIR)

        return target_dir


ORGANIZER = FileOrganizer(TARGETS_FILE)


def move_file(file: Path, target_dir: Path) -> None:
    """Move `file` and, if it exists, its sidecar file into `target_dir`."""

    shutil.move(file, target_dir)

    sidecar_file = file.with_suffix(".xmp")
    if sidecar_file.exists():
        shutil.move(sidecar_file, target_dir)


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
        move_file(file, root_dir / ORGANIZER.MISC_DIR)
        return

    file_ext = file.suffix
    if not file_ext:
        target_dir = ORGANIZER.get_extensionless_target(file)
        move_file(file, root_dir / target_dir)
        return

    file_ext = file_ext.lstrip(".").lower()
    if file_ext == "xmp":
        global xmp_files
        xmp_files.append(file)
        return

    target_dir = ORGANIZER.targets.get(file_ext, ORGANIZER.MISC_DIR)

    move_file(file, root_dir / target_dir)


def main(root_dir: Path) -> None:
    for directory in ORGANIZER.directories:
        (root_dir / directory).mkdir(parents=True, exist_ok=True)

    for file in root_dir.iterdir():
        move(file, root_dir)

    for xmp_file in xmp_files:
        try:
            move_file(xmp_file, root_dir / ORGANIZER.MISC_DIR)
        except FileNotFoundError:
            pass  # Do nothing if the image sidecar file had already been moved.
