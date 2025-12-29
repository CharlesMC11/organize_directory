"""Class for organizing files and directories."""

__author__ = "Charles Mesa Cayobit"

import logging
import os
import re
import shutil
from collections.abc import MutableMapping, MutableSet
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

        :param file: path to an ini file
        Expected headers are `destination_dirs`, `signature_patterns`, and `extensions_map`.
        """

        parser = ConfigParser()
        parser.read(file)

        try:
            destination_dirs = set(parser["destination_dirs"].values())

            re_pattern_groups = (
                f"(?P<{key}>{pattern})"
                for key, pattern in parser["signature_patterns"].items()
            )

            extensions_map = {
                ext: parser["destination_dirs"][dst_path]
                for ext, dst_path in parser["extensions_map"].items()
            }
        except KeyError:
            raise ValueError(
                "Config file does not have all the appropriate headers"
            )

        re_combined_pattern = "|".join(re_pattern_groups)
        re_compiled_pattern = re.compile(re_combined_pattern.encode("utf-8"))

        return cls(destination_dirs, re_compiled_pattern, extensions_map)

    # Magic methods

    def __init__(
            self,
            destination_dirs: MutableSet[str],
            signature_patterns: re.Pattern[bytes],
            extensions_map: MutableMapping[str, str],
    ) -> None:
        destination_dirs.add(self.MISC_DIR)
        extensions_map = {
            ext.lower(): path for ext, path in extensions_map.items()
        }

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

        if not root.is_dir():
            raise NotADirectoryError(f"{root} is not a directory")

        self._create_destination_dirs(root)

        # `move_file()` will move a file’s existing sidecar alongside it, so defer processing XMP files to the end.
        xmp_files: list[Path] = []

        def process(entry: os.DirEntry) -> None:
            if entry.is_dir():
                dst_path = self._get_unique_destination_path(
                    root / self.MISC_DIR / entry.name
                )
                shutil.move(entry, dst_path)
                return

            file = Path(entry)
            file_ext = file.suffix.lstrip(".").lower()
            if not file_ext:
                dst_dir = self.get_extensionless_dst(file)
                dst_path = self._get_unique_destination_path(
                    root / dst_dir / file.name
                )
                self.move_file_and_sidecar(file, dst_path)
                return

            elif file_ext == "xmp":
                nonlocal xmp_files
                xmp_files.append(file)
                return

            dst_dir = self.extensions_map.get(file_ext, self.MISC_DIR)
            dst_path = self._get_unique_destination_path(
                root / dst_dir / file.name
            )
            self.move_file_and_sidecar(file, dst_path)

        with os.scandir(root) as it:
            for entry in it:
                if entry.name in self.destination_dirs:
                    continue
                elif entry.name == ".DS_Store":
                    continue

                try:
                    process(entry)
                except (OSError, PermissionError) as e:
                    logger.error(f"Could not move {entry.name}: {e}")

        for xmp_file in xmp_files:
            if xmp_file.exists():
                try:
                    shutil.move(xmp_file, root / self.MISC_DIR / xmp_file.name)
                except (OSError, PermissionError) as e:
                    logger.error(f"Could not move {xmp_file.name}: {e}")

    # Public static methods

    @staticmethod
    def move_file_and_sidecar(src: Path, dst: Path) -> None:
        """Move a file and, if it exists, its sidecar from `src` into `dst`.

        :param src: the source file’s full path
        :param dst: the destination’s full path
        """

        shutil.move(src, dst)

        src_sidecar = src.with_suffix(".xmp")
        if src_sidecar.exists():
            dst_sidecar = dst.with_suffix(".xmp")
            shutil.move(src_sidecar, dst_sidecar)
        else:
            logger.debug(f"{src_sidecar} does not exist, skipping")

    # Private methods

    def _create_destination_dirs(self, root: Path) -> None:
        """Create the `destination_dirs`.

        :param root: the root directory
        """

        for dst in self.destination_dirs:
            (root / dst).mkdir(parents=True, exist_ok=True)

    # Private static methods

    @staticmethod
    def _get_unique_destination_path(path: Path):
        """Append a counter to the path stem if it’s not a unique path.

        :param path: a destination path to saved to
        """

        if not path.exists():
            return path

        stem = path.stem
        counter = 1
        while path.exists():
            path = path.with_stem(f"{stem}_{counter}")
            counter += 1

        return path
