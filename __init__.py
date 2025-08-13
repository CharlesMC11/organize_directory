__author__ = "Charles Mesa Cayobit"

from collections import defaultdict
from configparser import ConfigParser
from pathlib import Path
from types import MappingProxyType

_TARGETS_FILE = Path(__file__).with_name("targets.cfg")

MISC_DIR = "Misc"


def read_targets_from_file(
    file: Path,
) -> tuple[frozenset[str], MappingProxyType[str, str]]:
    """Read the directories and target paths from a file."""

    parser = ConfigParser()
    parser.read(file)

    directories = set(parser["directories"].values())
    directories.add(MISC_DIR)

    targets = defaultdict(
        lambda: MISC_DIR,
        {k: parser["directories"][v] for k, v in parser["targets"].items()},
    )

    return frozenset(directories), MappingProxyType(targets)


DIRECTORIES, TARGETS = read_targets_from_file(_TARGETS_FILE)


def move_file(file: Path, target_dir: Path) -> None:
    """Move `file` into `target_dir`."""

    file.rename(target_dir / file.name)


def move_extensionless(file: Path, root_dir: Path) -> None:
    """Move a file without an extension."""

    target_dir = MISC_DIR
    try:
        with file.open(encoding="utf-8") as f:
            header = f.readline().lower()

    except (IOError, UnicodeDecodeError):
        pass  # Do nothing because the target defaults to `MISC_DIR`

    else:
        if "python3" in header:
            target_dir = TARGETS["py"]

        elif "sh" in header:
            target_dir = TARGETS["sh"]

    move_file(file, root_dir / target_dir)


def move_image(image_file: Path, target_dir: Path) -> None:
    """Move an image and its sidecar file to `target_dir`."""

    move_file(image_file, target_dir)

    sidecar_file = image_file.with_suffix(".xmp")
    try:
        move_file(sidecar_file, target_dir)
    except FileNotFoundError:
        pass  # Do nothing if a sidecar file does not exist.


def main(root_dir: Path) -> None:
    for dir in DIRECTORIES:
        (root_dir / dir).mkdir(parents=True, exist_ok=True)

    # `move_image()` will move an image's existing sidecar file alongside the
    # image, so defer processing XMP files to the end.
    xmp_files: list[Path] = []

    for file in root_dir.iterdir():
        if file.name in DIRECTORIES or file.name == ".DS_Store":
            continue

        elif file.is_dir():
            move_file(file, root_dir / MISC_DIR)
            continue

        file_ext = file.suffix
        if not file_ext:
            move_extensionless(file, root_dir)
            continue

        file_ext = file_ext[1:].lower()
        if file_ext == "xmp":
            xmp_files.append(file)
            continue

        target_dir = TARGETS[file_ext]
        if target_dir == TARGETS["jpg"] or target_dir == TARGETS["dng"]:
            move_image(file, root_dir / target_dir)

        else:
            move_file(file, root_dir / target_dir)

    for xmp_file in xmp_files:
        try:
            move_file(xmp_file, root_dir / MISC_DIR)
        except FileNotFoundError:
            pass  # Do nothing if the image sidecar file had already been moved.


__all__ = "move_file", "move_extensionless", "move_image", "main"
