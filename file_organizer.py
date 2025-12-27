__author__ = "Charles Mesa Cayobit"

import logging
import re
import shutil
from configparser import ConfigParser
from pathlib import Path
from types import MappingProxyType

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class FileOrganizer:
    """Class for organizing files and directories."""

    # Class attributes

    MISC_DIR = "Misc"

    # Magic methods

    def __init__(self, targets_file: Path) -> None:
        parser = ConfigParser()
        parser.read(targets_file)

        directories = set(parser["directories"].values())
        directories.add(self.MISC_DIR)

        header_patterns = [
            (re.compile(pattern.encode()), key)
            for key, pattern in parser["header_patterns"].items()
        ]

        targets = {
            file_extension: parser["directories"][target_path]
            for file_extension, target_path in parser["targets"].items()
        }

        self._directories = directories
        self._header_patterns = header_patterns
        self._targets = targets

    # Properties

    @property
    def directories(self) -> frozenset[str]:
        return frozenset(self._directories)

    @property
    def header_patterns(self) -> tuple[tuple[re.Pattern[bytes], str], ...]:
        return tuple(self._header_patterns)

    @property
    def targets(self) -> MappingProxyType[str, str]:
        return MappingProxyType(self._targets)

    # Public methods

    def get_extensionless_target(self, file: Path) -> str:
        """Get the target directory for a file without an extension."""

        target_dir = self.MISC_DIR
        try:
            with file.open("rb") as f:
                header = f.read(256)

        except (IOError, PermissionError) as e:
            logger.error(f"Could not open file {file.name}: {e}")

        else:
            for pattern, key in self._header_patterns:
                if pattern.match(header):
                    return self._targets.get(key, self.MISC_DIR)

        return target_dir

    def organize(self, root_dir: Path) -> None:

        self._create_subdirectories(root_dir)

        # `move_file()` will move a file’s existing sidecar file alongside it, so defer processing XMP files to the end.
        xmp_files: list[Path] = []

        for file in root_dir.iterdir():
            if file.name in self._directories or file.name == ".DS_Store":
                continue

            elif file.is_dir():
                shutil.move(file, root_dir / self.MISC_DIR)
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

            target_dir = self._targets.get(file_ext, self.MISC_DIR)
            self.move_file(file, root_dir / target_dir)

        for xmp_file in xmp_files:
            if xmp_file.exists():
                shutil.move(xmp_file, root_dir / self.MISC_DIR)

    # Public static methods

    @staticmethod
    def move_file(file: Path, target_dir: Path) -> None:
        """Move `file` and, if it exists, its sidecar file into `target_dir`."""

        shutil.move(file, target_dir)

        sidecar_file = file.with_suffix(".xmp")
        if sidecar_file.exists():
            shutil.move(sidecar_file, target_dir)
        else:
            logger.debug(f"{sidecar_file} does not exist, skipping")

    # Private methods

    def _create_subdirectories(self, root_dir: Path) -> None:
        """Create the subdirectories listed in the config file."""

        for name in self._directories:
            (root_dir / name).mkdir(parents=True, exist_ok=True)
