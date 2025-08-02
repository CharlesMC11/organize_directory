__author__ = "Charles Mesa Cayobit"


from pathlib import Path

import organize_directory.targets as targets


def move_file(file: Path, target_dir: Path) -> None:
    """Move `file` into `target_dir`."""

    file.rename(target_dir / file.name)


def move_extensionless(file: Path, root_dir: Path) -> None:
    """Move a file without an extension."""

    target_dir = targets.MISC
    try:
        with file.open(encoding="utf-8") as f:
            header = f.readline().lower()

    except (IOError, UnicodeDecodeError):
        pass  # Do nothing because the target defaults to `MISC_DIR`

    else:
        if "python3" in header:
            target_dir = targets.PROGRAMMING_PYTHON

        elif "sh" in header:
            target_dir = targets.PROGRAMMING_SHELL

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
    for dir in targets.DIRECTORIES:
        (root_dir / dir).mkdir(parents=True, exist_ok=True)

    # `move_image()` will move an image's existing sidecar file alongside the
    # image, so defer processing XMP files to the end.
    xmp_files: list[Path] = []

    for file in root_dir.iterdir():
        if file.name in targets.DIRECTORIES or file.name == ".DS_Store":
            continue

        elif file.is_dir():
            move_file(file, root_dir / targets.MISC)
            continue

        file_ext = file.suffix
        if not file_ext:
            move_extensionless(file, root_dir)
            continue

        file_ext = file_ext[1:].lower()
        if file_ext == "xmp":
            xmp_files.append(file)
            continue

        target_dir = targets.TARGETS[file_ext]
        if target_dir == targets.IMAGES or target_dir == targets.IMAGES_RAW:
            move_image(file, root_dir / target_dir)

        else:
            move_file(file, root_dir / target_dir)

    for xmp_file in xmp_files:
        try:
            move_file(xmp_file, root_dir / targets.MISC)
        except FileNotFoundError:
            pass  # Do nothing if the image sidecar file had already been moved.


__all__ = "move_file", "move_extensionless", "move_image", "main"
