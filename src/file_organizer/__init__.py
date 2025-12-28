"""Class for organizing files and directories."""

__author__ = "Charles Mesa Cayobit"

import logging
import os
import re
import shutil
from collections.abc import Iterable, Mapping
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
        """Use mappings from an ini file.

        Expected headers are `destination_dirs`, `signature_patterns`, and `extensions_map`.
        """
        parser = ConfigParser()
        parser.read(file)

        destination_dirs = set(parser["destination_dirs"].values())
        destination_dirs.add(cls.MISC_DIR)

        re_pattern_groups = (
            f"(?P<{key}>{pattern})"
            for key, pattern in parser["signature_patterns"].items()
        )
        re_combined_pattern = "|".join(re_pattern_groups)
        re_compiled_pattern = re.compile(re_combined_pattern.encode("utf-8"))

        extensions_map = {
            file_extension.lower(): parser["destination_dirs"][target_path]
            for file_extension, target_path in parser["extensions_map"].items()
        }

        return cls(destination_dirs, re_compiled_pattern, extensions_map)

    # Magic methods

    def __init__(
            self,
            destination_dirs: Iterable[str],
            signature_patterns: re.Pattern[bytes],
            extensions_map: Mapping[str, str],
    ) -> None:
        """Load the organizer’s configurations."""
        self.destination_dirs: Final = frozenset(destination_dirs)
        self.signature_patterns: Final = signature_patterns
        self.extensions_map: Final = MappingProxyType(extensions_map)

    # Public methods

    def get_extensionless_dst(self, file: Path) -> str:
        """Get the target directory for a file without an extension."""
        destination_dir = self.MISC_DIR
        try:
            with file.open("rb") as f:
                header = f.read(32)

        except (OSError, PermissionError) as e:
            logger.error(f"Could not open file {file.name}: {e}")

        else:
            match = self.signature_patterns.match(header)
            if match is not None:
                key = match.lastgroup
                if key is not None:
                    return self.extensions_map.get(key, self.MISC_DIR)

        return destination_dir

    def organize(self, root: Path) -> None:
        """Organize the contents of `root`."""
        self._create_destination_dirs(root)

        # `move_file()` will move a file’s existing sidecar alongside it, so defer processing XMP files to the end.
        xmp_files: list[Path] = []

        with os.scandir(root) as it:
            for entry in it:
                if entry.name in self.destination_dirs:
                    continue

                elif entry.name == ".DS_Store":
                    continue

                elif entry.is_dir():
                    shutil.move(entry, root / self.MISC_DIR / entry.name)
                    continue

                file = Path(entry)
                file_ext = file.suffix.lstrip(".").lower()
                if not file_ext:
                    destination_dir = self.get_extensionless_dst(file)
                    self.move_file_and_sidecar(
                        file, root / destination_dir / file.name
                    )
                    continue

                elif file_ext == "xmp":
                    xmp_files.append(file)
                    continue

                destination_dir = self.extensions_map.get(
                    file_ext, self.MISC_DIR
                )

                self.move_file_and_sidecar(
                    file, root / destination_dir / file.name
                )

        for xmp_file in xmp_files:
            if xmp_file.exists():
                shutil.move(xmp_file, root / self.MISC_DIR / xmp_file.name)

    # Public static methods

    @staticmethod
    def move_file_and_sidecar(src: Path, dst: Path) -> None:
        """Move a file and, if it exists, its sidecar from `src` into `dst`."""

        dst = FileOrganizer._get_unique_path(dst)

        shutil.move(src, dst)

        src_sidecar = src.with_suffix(".xmp")
        if src_sidecar.exists():
            dst_sidecar = dst.with_suffix(".xmp")
            shutil.move(src_sidecar, dst_sidecar)
        else:
            logger.debug(f"{src_sidecar} does not exist, skipping")

    # Private methods

    def _create_destination_dirs(self, root: Path) -> None:
        """Create the `destination_dirs` listed in the config file."""
        for dst in self.destination_dirs:
            (root / dst).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _get_unique_path(path: Path):
        if not path.exists():
            return path

        stem = path.stem
        counter = 1
        while path.exists():
            path = path.with_stem(f"{stem}_{counter}")
            counter += 1

        return path
