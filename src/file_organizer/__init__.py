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


class FileOrganizerError(Exception): ...


class InvalidConfigError(FileOrganizerError, ValueError):
    """Raised when an invalid config file is used."""


class MissingRequiredFieldsError(InvalidConfigError):
    """Raised when required fields in a config file are missing."""


class NamingAttemptsExceededError(FileOrganizerError):
    """Raised when a unique filename cannot be generated."""


class FileOrganizer:
    """Class for organizing files and directories."""

    # Class attributes

    MISC_DIR: Final = "Misc"
    MAX_UNIQUE_PATH_ATTEMPTS: Final = 1_000

    _CONFIG_REQUIRED_FIELDS: Final = frozenset(
        {"destination_dirs", "extensions_map"}
    )

    CONFIG_ENCODING = "utf-8"
    _GROUP_PATTERN_NAME_SANITIZER: Final = re.compile(r"\W")

    # Class methods

    @classmethod
    def from_ini(cls, file: Path) -> Self:
        """Initialize the organizer using an INI configuration file.

        :param file: path to an ini file

        Required sections are `destination_dirs`, `signature_patterns`, and `extensions_map`.
        """

        parser = ConfigParser()
        with cls._read_validated_config(file) as f:
            parser.read_file(f)

        cls._validate_config_required_fields(parser.sections())

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
        """Initialize the organizer using a JSON configuration file.

        :param file: path to a JSON file
        :raises InvalidConfigError: if config file
        Required sections are `destination_dirs`, `signature_patterns`, and `extensions_map`.
        """

        with cls._read_validated_config(file) as f:
            content = json.load(f)

        cls._validate_config_required_fields(content.keys())

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

        validated_map = {}
        for ext, dst in extensions_map.items():
            sanitized_ext = ext.lower()

            # `dst` has be an existing entry in `unique_dst_dirs`
            if dst in unique_dst_dirs:
                validated_map[sanitized_ext] = dst
            else:
                logger.warning(f"{dst} not in `destination_dirs`, ignoring.")

        compiled_pattern = None
        if signature_patterns:
            patterns = []
            pattern_groups = {}

            encoding = FileOrganizer.CONFIG_ENCODING
            # `ext` has to be an existing key in `normalized_map`
            for ext, pattern in signature_patterns.items():
                sanitized_ext = ext.lower()
                unescaped_pattern = pattern.encode(encoding).decode(
                    "unicode_escape"
                )

                if sanitized_ext not in validated_map:
                    logger.warning(
                        f"{sanitized_ext} not in `extensions_map`, ignoring."
                    )
                    continue

                group_name = "g_" + self._GROUP_PATTERN_NAME_SANITIZER.sub(
                    "_", sanitized_ext
                )
                pattern_groups[group_name] = sanitized_ext
                patterns.append(f"(?P<{group_name}>{unescaped_pattern})")

            combined_pattern = "|".join(patterns)
            compiled_pattern = re.compile(combined_pattern.encode("latin-1"))

            self._pattern_map: Final = MappingProxyType(pattern_groups)

        self.destination_dirs: Final = frozenset(unique_dst_dirs)
        self.signature_patterns: Final = compiled_pattern
        self.extensions_map: Final = MappingProxyType(validated_map)

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
            with file.open("r", encoding=FileOrganizer.CONFIG_ENCODING) as f:
                yield f
        except (FileNotFoundError, IsADirectoryError) as e:
            raise FileNotFoundError(f"No such file: '{file.name}'") from e
        except PermissionError:
            raise
        except MissingRequiredFieldsError:
            raise
        except Exception:
            raise

    @classmethod
    def _validate_config_required_fields(cls, keys: Collection[str]) -> None:
        """Validate the required fields of a config file.

        :raises InvalidConfigError: if any of the required fields are missing
        """

        if missing := cls._CONFIG_REQUIRED_FIELDS - frozenset(keys):
            message = "Missing required sections: " + ", ".join(missing)
            raise MissingRequiredFieldsError(message)

    def _create_destination_dirs(self, root: Path) -> None:
        """Create the `destination_dirs`.

        :param root: the root directory
        :raises PermissionError: if `root` cannot be accessed
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
            logger.error(f"Could not open file '{file.name}': {e}")
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

        A sidecar file is moved only if its main file is moved successfully.
        If moving the sidecar file fails, the process continues.

        :param src: the source file’s full path
        :param dst: the destination’s full path
        """

        dst = FileOrganizer._try_move(src, dst)
        if dst is None:
            return

        src_sidecar = src.with_suffix(".xmp")
        if src_sidecar.exists():
            dst_sidecar = dst.with_suffix(".xmp")
            try:
                # Overwrite existing sidecars in a destination dir
                shutil.move(src_sidecar, dst_sidecar)
            except OSError as e:
                logger.warning(f"Failed to move: '{src_sidecar.name}': {e}")

    @staticmethod
    def _try_move(src: Path, dst: Path) -> Path | None:
        """Attempt to move `src` to a unique `dst` path.

        :param src: the source file’s full path
        :param dst: the destination’s full path
        :return: the final destination path if the move succeeds, `None` if an OSError is raised
        """

        try:
            dst = FileOrganizer._get_unique_destination_path(dst)
            shutil.move(src, dst)
        except NamingAttemptsExceededError as e:
            msg = f"Failed to create a unique name for '{src.name}': {e}"
            logger.warning(msg)
            return None
        except OSError as e:
            logger.warning(f"Failed to move '{src.name}': {e}")
            return None
        else:
            return dst

    @staticmethod
    def _get_unique_destination_path(path: Path) -> Path:
        """Generate a unique destination path.

        If the destination path exists, the following suffixes are appended for each attempt:
        1. Date (%Y%m%d)
        2. Time (%H%M%S)
        3. Microseconds (%f)
        4. Padded counter (1 to FileOrganizer.MAX_UNIQUE_PATH_ATTEMPTS)

        :param path: a destination path to saved to
        :return: a guaranteed unique path
        :raises NamingAttemptsExceededError: if no unique path can be generated with the attempted limit
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

        raise NamingAttemptsExceededError(
            f"Could not create a unique filename for {path.name} "
            f"after {max_attempts:,} attempts"
        )
