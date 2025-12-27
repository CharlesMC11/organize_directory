__author__ = "Charles Mesa Cayobit"

import re
import shutil
from configparser import ConfigParser
from pathlib import Path


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
        """Get the target directory for a file without an extension."""

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

    def organize(self, root_dir: Path) -> None:
        for directory in self.directories:
            (root_dir / directory).mkdir(parents=True, exist_ok=True)

        # `move_file()` will move a file’s existing sidecar file alongside it, so defer processing XMP files to the end.
        xmp_files: list[Path] = []

        for file in root_dir.iterdir():
            if file.name in self.directories or file.name == ".DS_Store":
                continue

            elif file.is_dir():
                self.move_file(file, root_dir / self.MISC_DIR)
                continue

            file_ext = file.suffix
            if not file_ext:
                target_dir = self.get_extensionless_target(file)
                self.move_file(file, root_dir / target_dir)
                continue

            file_ext = file_ext.lstrip(".").lower()
            if file_ext == "xmp":
                xmp_files.append(file)
                continue

            target_dir = self.targets.get(file_ext, self.MISC_DIR)
            self.move_file(file, root_dir / target_dir)

        for xmp_file in xmp_files:
            if xmp_file.exists():
                self.move_file(xmp_file, root_dir / self.MISC_DIR)

    @staticmethod
    def move_file(file: Path, target_dir: Path) -> None:
        """Move `file` and, if it exists, its sidecar file into `target_dir`."""

        shutil.move(file, target_dir)

        sidecar_file = file.with_suffix(".xmp")
        if sidecar_file.exists():
            shutil.move(sidecar_file, target_dir)
