__author__ = "Charles Mesa Cayobit"

import logging
import re
import shutil
from collections.abc import Mapping, Iterable, Sequence
from configparser import ConfigParser
from pathlib import Path
from types import MappingProxyType
from typing import Final, Self

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class FileOrganizer:
    """Class for organizing files and directories."""

    # Class attributes

    MISC_DIR: Final = "Misc"

    # Class methods

    @classmethod
    def from_ini(cls, file: Path) -> Self:
        parser = ConfigParser()
        parser.read(file)

        destination_dirs = set(parser["destination_dirs"].values())
        destination_dirs.add(cls.MISC_DIR)

        signature_patterns = [
            (re.compile(pattern.encode()), key)
            for key, pattern in parser["signature_patterns"].items()
        ]

        extensions_map = {
            file_extension: parser["destination_dirs"][target_path]
            for file_extension, target_path in parser["extensions_map"].items()
        }

        return cls(destination_dirs, signature_patterns, extensions_map)

    # Magic methods

    def __init__(
            self,
            destination_dirs: Iterable[str],
            signature_patterns: Sequence[tuple[re.Pattern[bytes], str]],
            extensions_map: Mapping[str, str],
    ) -> None:
        self.destination_dirs: Final = frozenset(destination_dirs)
        self.signature_patterns: Final = tuple(signature_patterns)
        self.extensions_map: Final = MappingProxyType(extensions_map)

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
            for pattern, key in self.signature_patterns:
                if pattern.match(header):
                    return self.extensions_map.get(key, self.MISC_DIR)

        return target_dir

    def organize(self, root_dir: Path) -> None:
        """Organize the contents of `root_dir`."""

        self._create_destination_dirs(root_dir)

        # `move_file()` will move a file’s existing sidecar file alongside it, so defer processing XMP files to the end.
        xmp_files: list[Path] = []

        for file in root_dir.iterdir():
            if file.name in self.destination_dirs or file.name == ".DS_Store":
                continue

            elif file.is_dir():
                shutil.move(file, root_dir / self.MISC_DIR)
                continue

            file_ext = file.suffix
            if not file_ext:
                target_dir = self.get_extensionless_target(file)
                self.move_file(file, root_dir / target_dir)
                target_dir = self.get_extensionless_dst(file)
                continue

            file_ext = file_ext.lstrip(".").lower()
            if file_ext == "xmp":
                xmp_files.append(file)
                continue

            target_dir = self.extensions_map.get(file_ext, self.MISC_DIR)
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

    def _create_destination_dirs(self, root_dir: Path) -> None:
        """Create the destination_dirs listed in the config file."""

        for name in self.destination_dirs:
            (root_dir / name).mkdir(parents=True, exist_ok=True)
