"""Class for organizing files and directories."""

__author__ = "Charles Mesa Cayobit"

import json
import logging
import os
import re
import shutil
from collections.abc import Collection, Generator, Mapping
from configparser import ConfigParser
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType
from typing import Final, NoReturn, Self, TextIO

logger = logging.getLogger(__name__)

DEFAULT_ENCODING = "utf-8"


class InvalidConfigError(ValueError):
    """Raised when an invalid config file is used."""


class FileOrganizer:
    """Class for organizing files and directories."""

    # Class attributes

    MISC_DIR: Final = "Misc"

    # Class methods

    @classmethod
    def from_ini(cls, file: Path) -> Self:
        """Use mappings from an ini file.

        :param file: path to an ini file
        Required sections are `destination_dirs`, `signature_patterns`, and `extensions_map`.
        """

        parser = ConfigParser()
        with cls._read_validated_config(file) as f:
            parser.read_file(f)

        required_sections = {
            "destination_dirs",
            "signature_patterns",
            "extensions_map",
        }
        missing_sections = required_sections - frozenset(parser.sections())
        if missing_sections:
            message = "Missing required sections: " + ", ".join(
                missing_sections
            )
            raise InvalidConfigError(message)

        destination_dirs = parser["destination_dirs"].values()

        signature_patterns = parser["signature_patterns"]

        extensions_map = {
            ext: parser["destination_dirs"][key]
            for ext, key in parser["extensions_map"].items()
        }

        return cls(destination_dirs, signature_patterns, extensions_map)

    @classmethod
    def from_json(cls, file: Path) -> Self:
        """Use mappings from a JSON file.

        :param file: path to a JSON file
        Required sections are `destination_dirs`, `signature_patterns`, and `extensions_map`.
        """

        with cls._read_validated_config(file) as f:
            content = json.load(f)

        destination_dirs = content["destination_dirs"].values()

        signature_patterns = content["signature_patterns"]

        extensions_map = {}
        for key, extensions in content["extensions_map"].items():
            for ext in extensions:
                extensions_map[ext] = content["destination_dirs"][key]

        return cls(destination_dirs, signature_patterns, extensions_map)

    # Magic methods

    def __init__(
            self,
            destination_dirs: Collection[str],
            signature_patterns: Mapping[str, str],
            extensions_map: Mapping[str, str],
    ) -> None:
        unique_dst_dirs = {self.MISC_DIR}
        unique_dst_dirs.update(destination_dirs)

        combined_pattern = "|".join(
            f"(?P<{key.lower()}>{pattern})"
            for key, pattern in signature_patterns.items()
        )
        compiled_pattern = re.compile(
            combined_pattern.encode(DEFAULT_ENCODING)
        )

        extensions_map = {
            ext.lower(): path for ext, path in extensions_map.items()
        }

        self.destination_dirs: Final = frozenset(unique_dst_dirs)
        self.signature_patterns: Final = compiled_pattern
        self.extensions_map: Final = MappingProxyType(extensions_map)

    # Public methods

    def get_extensionless_dst(self, file: Path) -> str:
        """Get the target directory for a file without an extension."""

        try:
            with file.open("rb") as f:
                header = f.read(32)
        except OSError as e:
            logger.error(f"Could not open file {file.name}: {e}")
            return self.MISC_DIR

        match = self.signature_patterns.match(header)
        if match is None:
            return self.MISC_DIR

        key = match.lastgroup
        if key is None:
            return self.MISC_DIR

        return self.extensions_map.get(key, self.MISC_DIR)

    def organize(self, root: Path) -> None:
        """Organize the contents of `root`."""

        self._create_destination_dirs(root)

        # `move_file_and_sidecar()` will move a file’s existing sidecar alongside it, so defer processing the rest XMP files to the end.
        xmp_files: list[Path] = []

        with os.scandir(root) as it:
            for entry in it:
                if entry.name in self.destination_dirs:
                    continue

                elif entry.name == ".DS_Store":
                    continue

                elif entry.is_dir():
                    dst_path = root / self.MISC_DIR / entry.name
                    self._safely_move(entry, dst_path)
                    continue

                file = Path(entry.path)
                file_ext = file.suffix.lstrip(".").lower()
                if file_ext == "xmp":
                    xmp_files.append(file)
                    continue

                elif not file_ext:
                    dst_dir = self.get_extensionless_dst(file)
                else:
                    dst_dir = self.extensions_map.get(file_ext, self.MISC_DIR)

                dst_path = root / dst_dir / file.name
                self.move_file_and_sidecar(file, dst_path)

        for xmp_file in xmp_files:
            if xmp_file.exists():
                dst_path = root / self.MISC_DIR / xmp_file.name
                self._safely_move(xmp_file, dst_path)
            else:
                logger.info(xmp_file.name + " has already been moved")

    # Public static methods

    @staticmethod
    def move_file_and_sidecar(src: Path, dst: Path) -> None:
        """Move a file and, if it exists, its sidecar from `src` into `dst`.

        :param src: the source file’s full path
        :param dst: the destination’s full path
        """

        if not FileOrganizer._safely_move(src, dst):
            return

        src_sidecar = src.with_suffix(".xmp")
        if not src_sidecar.exists():
            logger.info(src_sidecar.name + " does not exist, skipping")
            return

        dst_sidecar = dst.with_suffix(".xmp")
        # Overwrite existing sidecars in a destination dir
        shutil.move(src_sidecar, dst_sidecar)

    # Private methods

    def _create_destination_dirs(self, root: Path) -> None:
        """Create the `destination_dirs`.

        :param root: the root directory
        """

        if not root.is_dir():
            raise NotADirectoryError(f"Not a directory: '{root.name}'")

        try:
            for dst in self.destination_dirs:
                (root / dst).mkdir(parents=True, exist_ok=True)
        except PermissionError:
            raise

    # Private static methods

    @staticmethod
    @contextmanager
    def _read_validated_config(
            file: Path,
    ) -> Generator[TextIO, None, None] | NoReturn:
        try:
            with file.open("r", encoding=DEFAULT_ENCODING) as f:
                yield f
        except (FileNotFoundError, IsADirectoryError):
            raise InvalidConfigError(f"No such file: '{file.name}'")
        except PermissionError:
            raise InvalidConfigError(f"Permission denied: '{file.name}'")
        except Exception as e:
            raise InvalidConfigError(f"Invalid config: '{file.name}': {e}")

    @staticmethod
    def _get_unique_destination_path(path: Path) -> Path:
        """Append a counter to `path`’s stem if it’s not a unique path.

        :param path: a destination path to saved to
        :return: a path with a unique stem
        """

        if not path.exists():
            return path

        stem = path.stem
        counter = 1
        while path.exists():
            path = path.with_stem(f"{stem}_{counter}")
            counter += 1

        return path

    @staticmethod
    def _safely_move(src: Path, dst: Path) -> bool:
        """Move `src` to `dst` while ensuring `dst` doesn’t exist.

        :param src: the source file’s full path
        :param dst: the destination’s full path
        :return: if the move was successful or not
        """

        try:
            shutil.move(src, FileOrganizer._get_unique_destination_path(dst))
        except OSError as e:
            logger.warning(f"Could not move {src.name}: {e}")

            return False
        return True
