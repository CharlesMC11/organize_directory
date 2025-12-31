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
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Final, Self, TextIO

logger = logging.getLogger(__name__)

DEFAULT_ENCODING = "utf-8"


class InvalidConfigError(ValueError):
    """Raised when an invalid config file is used."""


class FileOrganizer:
    """Class for organizing files and directories."""

    # Class attributes

    MISC_DIR: Final = "Misc"
    MAX_UNIQUE_PATH_ATTEMPTS: Final = 1_000

    _CONFIG_REQUIRED_FIELDS: Final = frozenset(
        {"destination_dirs", "extensions_map"}
    )

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

        cls._validate_required_fields(parser.sections())

        destination_dirs = parser["destination_dirs"].values()

        extensions_map = {
            ext: parser["destination_dirs"][key]
            for ext, key in parser["extensions_map"].items()
        }

        signature_patterns = None
        if "signature_patterns" in parser:
            signature_patterns = parser["signature_patterns"]

        return cls(destination_dirs, extensions_map, signature_patterns)

    @classmethod
    def from_json(cls, file: Path) -> Self:
        """Use mappings from a JSON file.

        :param file: path to a JSON file
        Required sections are `destination_dirs`, `signature_patterns`, and `extensions_map`.
        """

        with cls._read_validated_config(file) as f:
            content = json.load(f)

        cls._validate_required_fields(content.keys())

        destination_dirs = content["destination_dirs"].values()

        extensions_map = {}
        for key, extensions in content["extensions_map"].items():
            for ext in extensions:
                extensions_map[ext] = content["destination_dirs"][key]

        signature_patterns = None
        if "signature_patterns" in content:
            signature_patterns = content["signature_patterns"]

        return cls(destination_dirs, extensions_map, signature_patterns)

    # Magic methods

    def __init__(
            self,
            destination_dirs: Collection[str],
            extensions_map: Mapping[str, str],
            signature_patterns: Mapping[str, str] | None = None,
    ) -> None:
        unique_dst_dirs = {self.MISC_DIR}
        unique_dst_dirs.update(destination_dirs)

        normalized_map = {}
        for ext, dst in extensions_map.items():
            normalized_ext = ext.lower()

            # `dst` has be an existing entry in `unique_dst_dirs`
            if dst in unique_dst_dirs:
                normalized_map[normalized_ext] = dst

        compiled_pattern = None
        if signature_patterns:
            patterns = []
            pattern_groups = {}

            # `ext` has to be an existing key in `normalized_map`
            for ext, pattern in signature_patterns.items():
                normalized_ext = ext.lower()

                if normalized_ext not in normalized_map:
                    continue

                group_name = "g_" + normalized_ext
                pattern_groups[group_name] = normalized_ext
                patterns.append(f"(?P<{group_name}>{pattern})")

            combined_pattern = "|".join(patterns)
            compiled_pattern = re.compile(
                combined_pattern.encode(DEFAULT_ENCODING)
            )

            self._pattern_map: Final = MappingProxyType(pattern_groups)

        self.destination_dirs: Final = frozenset(unique_dst_dirs)
        self.signature_patterns: Final = compiled_pattern
        self.extensions_map: Final = MappingProxyType(normalized_map)

    # Public methods

    def organize(self, root: Path) -> None:
        """Organize the contents of `root`."""

        self._create_destination_dirs(root)

        # `move_file_and_sidecar()` will move a file’s existing sidecar alongside it, so defer processing the rest XMP files to the end.
        xmp_files: list[Path] = []

        with os.scandir(root) as it:
            for entry in it:
                if entry.name == ".DS_Store" or entry.is_symlink():
                    continue

                elif entry.is_dir():
                    if entry.name in self.destination_dirs:
                        continue

                    dst_path = root / self.MISC_DIR / entry.name
                    self._try_move(Path(entry.path), dst_path)
                    continue

                elif not entry.is_file():
                    continue

                file = Path(entry.path)
                file_ext = file.suffix.lstrip(".").lower()
                if file_ext == "xmp":
                    xmp_files.append(file)
                    continue

                elif not file_ext:
                    dst_dir = self._get_extensionless_dst(file)
                else:
                    dst_dir = self.extensions_map.get(file_ext, self.MISC_DIR)

                dst_path = root / dst_dir / file.name
                self._move_file_and_sidecar(file, dst_path)

        for xmp_file in xmp_files:
            if xmp_file.exists():
                dst_path = root / self.MISC_DIR / xmp_file.name
                self._try_move(xmp_file, dst_path)
            else:
                logger.info(xmp_file.name + " has already been moved")

    # Private methods

    @staticmethod
    @contextmanager
    def _read_validated_config(file: Path) -> Generator[TextIO, None, None]:
        try:
            with file.open("r", encoding=DEFAULT_ENCODING) as f:
                yield f
        except (FileNotFoundError, IsADirectoryError):
            raise InvalidConfigError(f"No such file: '{file.name}'")
        except PermissionError:
            raise InvalidConfigError(f"Permission denied: '{file.name}'")
        except Exception as e:
            raise InvalidConfigError(f"Invalid config: '{file.name}': {e}")

    @classmethod
    def _validate_required_fields(cls, keys: Collection[str]) -> None:
        if missing := cls._CONFIG_REQUIRED_FIELDS - frozenset(keys):
            message = "Missing required sections: " + ", ".join(missing)
            raise InvalidConfigError(message)

    def _create_destination_dirs(self, root: Path) -> None:
        """Create the `destination_dirs`.

        :param root: the root directory
        """

        if not root.is_dir():
            raise NotADirectoryError(f"Not a directory: '{root.name}'")

        try:
            for dst in self.destination_dirs:
                (root / dst).mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            e.strerror = f"Permission denied: '{root.name}'"
            raise e

    def _get_extensionless_dst(self, file: Path) -> str:
        """Get the target directory for a file without an extension."""

        if self.signature_patterns is None:
            return self.MISC_DIR

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

        return self.extensions_map.get(self._pattern_map[key], self.MISC_DIR)

    @staticmethod
    def _move_file_and_sidecar(src: Path, dst: Path) -> None:
        """Move a file and, if it exists, its sidecar from `src` into `dst`.

        :param src: the source file’s full path
        :param dst: the destination’s full path
        """

        if not FileOrganizer._try_move(src, dst):
            return

        src_sidecar = src.with_suffix(".xmp")
        if not src_sidecar.exists():
            logger.info(src_sidecar.name + " does not exist, skipping")
            return

        dst_sidecar = dst.with_suffix(".xmp")
        # Overwrite existing sidecars in a destination dir
        shutil.move(src_sidecar, dst_sidecar)

    @staticmethod
    def _try_move(src: Path, dst: Path) -> bool:
        """Attempt to move `src` to a unique `dst` path.

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

    @staticmethod
    def _get_unique_destination_path(path: Path) -> Path:
        """Append a counter to `path`’s stem if it’s not a unique path.

        :param path: a destination path to saved to
        :return: a path with a unique stem
        """

        if not path.exists():
            return path

        stem = path.stem
        timestamp = datetime.now()
        fmt = "%Y%m%d"

        new_path = path.with_stem(f"{stem}_{timestamp.strftime(fmt)}")
        if not new_path.exists():
            return new_path

        fmt += "_%H%M%S"
        new_path = path.with_stem(f"{stem}_{timestamp.strftime(fmt)}")
        if not new_path.exists():
            return new_path

        fmt += "_%f"
        new_path = path.with_stem(f"{stem}_{timestamp.strftime(fmt)}")
        if not new_path.exists():
            return new_path

        stem = new_path.stem
        max_attempts = FileOrganizer.MAX_UNIQUE_PATH_ATTEMPTS
        padding = len(str(max_attempts))
        for n in range(1, max_attempts):
            new_path = new_path.with_stem(f"{stem}_{n:0{padding}}")
            if not new_path.exists():
                return new_path

        raise RuntimeError(
            f"Could not create a unique filename for {path.name} "
            f"after {max_attempts:,} attempts"
        )
