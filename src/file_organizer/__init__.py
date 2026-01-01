"""Class for organizing files and directories."""

import errno
import json
import logging
import re
from collections.abc import Collection, Generator, Mapping
from configparser import ConfigParser
from contextlib import contextmanager
from itertools import count, islice
from pathlib import Path
from time import sleep
from types import MappingProxyType
from typing import Final, TextIO

logger = logging.getLogger(__name__)


class FileOrganizerError(Exception): ...


class InvalidConfigError(FileOrganizerError, ValueError):
    """Raised when an invalid config file is used."""


class MissingRequiredFieldsError(InvalidConfigError):
    """Raised when required fields in a config file are missing."""


class FileOrganizer:
    """Class for organizing files and directories."""

    # Class attributes

    CONFIG_FILE_ENCODING: Final = "utf-8"

    FALLBACK_DIR_NAME: Final = "Misc"

    SIGNATURE_READ_SIZE: Final = 32

    # Private class attributes

    _CONFIG_REQUIRED_FIELDS: Final = frozenset(
        {"destination_dirs", "extensions_map"}
    )

    _GROUP_PATTERN_NAME_SANITIZER: Final = re.compile(r"\W")

    _IGNORED_FILES: Final = frozenset({".DS_Store", ".localized"})

    _TRANSIENT_ERRORS: Final = frozenset(
        {errno.EAGAIN, errno.EBUSY, errno.ETIMEDOUT}
    )
    _MAX_MOVE_RETRIES: Final = 3
    _RETRY_DELAY: Final = 0.5

    _MAX_PATH_COLLISION_RESOLUTIONS: Final = 99

    # Class methods

    @classmethod
    def from_ini(cls, file: Path) -> FileOrganizer:
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
    def from_json(cls, file: Path) -> FileOrganizer:
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
        unique_dst_dirs = {self.FALLBACK_DIR_NAME, *destination_dirs}

        validated_map: dict[str, str] = {}
        for ext, dst in extensions_map.items():
            if not (sanitized_ext := self._sanitize_file_extension(ext)):
                msg = f"Sanitized file extension '{ext}' is empty, skipping."
                logger.warning(msg)
                continue

            # `dst` has be an existing entry in `unique_dst_dirs`
            if dst in unique_dst_dirs:
                if sanitized_ext in validated_map:
                    msg = f"{sanitized_ext} already exists in {validated_map}, updating value."
                    logger.warning(msg)
                validated_map[sanitized_ext] = dst
            else:
                logger.warning(f"{dst} not in `destination_dirs`, ignoring.")

        self.destination_dirs: Final = frozenset(unique_dst_dirs)
        self.extensions_map: Final = MappingProxyType(validated_map)

        self.signature_patterns = None
        if signature_patterns and (
            patterns := self._compile_signature_patterns(
                validated_map.keys(), signature_patterns
            )
        ):
            self.signature_patterns, self._pattern_map = patterns

    # Public methods

    def organize(self, root: Path) -> None:
        """Organize the contents of `root`."""

        self._create_destination_dirs(root)

        # `move_file_and_sidecar()` will move a file’s existing sidecar alongside it, so defer processing the rest XMP files to the end.
        xmp_files: list[Path] = []
        moved_xmp_files: set[str] = set()

        for entry in root.iterdir():
            match self._determine_dst(entry):
                case None:
                    continue

                case "DEFER":
                    xmp_files.append(entry)

                case dst_dir:
                    dst_path = root / dst_dir

                    if entry.info.is_dir():
                        self._try_move_into(entry, dst_path)

                    else:
                        _, xmp = self._move_file_and_sidecar(entry, dst_path)

                        if xmp is not None:
                            moved_xmp_files.add(xmp.name)

        xmp_dst = root / self.FALLBACK_DIR_NAME
        for xmp in xmp_files:
            if not xmp.name in moved_xmp_files:
                self._try_move_into(xmp, xmp_dst)
            else:
                logger.info(xmp.name + " has already been moved")

    # Private methods

    @classmethod
    @contextmanager
    def _read_validated_config(
        cls, file: Path
    ) -> Generator[TextIO, None, None]:
        try:
            with file.open("r", encoding=cls.CONFIG_FILE_ENCODING) as f:
                yield f
        except (FileNotFoundError, IsADirectoryError) as e:
            raise FileNotFoundError from e

    @staticmethod
    def _validate_config_required_fields(keys: Collection[str]) -> None:
        """Validate the required fields of a config file.

        :raises InvalidConfigError: if any of the required fields are missing
        """

        if missing := FileOrganizer._CONFIG_REQUIRED_FIELDS - frozenset(keys):
            message = "Missing required sections: " + ", ".join(missing)
            raise MissingRequiredFieldsError(message)

    @staticmethod
    def _sanitize_file_extension(ext: str) -> str:
        if ext := ext.strip(" .").lower():
            return "." + ext
        return ""

    def _compile_signature_patterns(
        self,
        validated_extensions: Collection[str],
        signature_patterns: Mapping[str, str],
    ) -> tuple[re.Pattern[bytes], MappingProxyType[str, str]] | None:
        """Compile file signature patterns into one bytes pattern"""

        pattern_groups: list[str] = []
        pattern_name_map: dict[str, str] = {}

        encoding = self.CONFIG_FILE_ENCODING
        # `ext` has to be an existing key in `normalized_map`
        for ext, pattern in signature_patterns.items():
            if not (sanitized_ext := self._sanitize_file_extension(ext)):
                msg = f"Sanitized file extension '{ext}' is empty, skipping."
                logger.warning(msg)
                continue
            elif sanitized_ext not in validated_extensions:
                msg = f"{sanitized_ext} not in `extensions_map`, ignoring."
                logger.warning(msg)
                continue

            unescaped_pattern = pattern.encode(encoding).decode(
                "unicode_escape"
            )
            try:
                unescaped_pattern.encode("latin-1")
            except UnicodeError, re.error:
                logger.warning(f"Invalid pattern '{pattern}', skipping.")
                continue

            group_name = "g_" + self._GROUP_PATTERN_NAME_SANITIZER.sub(
                "_", sanitized_ext
            )
            pattern_name_map[group_name] = sanitized_ext
            pattern_groups.append(f"(?P<{group_name}>(?>{unescaped_pattern}))")

        if not pattern_groups:
            return None

        combined_pattern = "|".join(pattern_groups)
        compiled_pattern = re.compile(
            combined_pattern.encode("latin-1"), re.NOFLAG
        )

        return compiled_pattern, MappingProxyType(pattern_name_map)

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
        except PermissionError, OSError:
            raise

    def _determine_dst(self, entry: Path) -> str | None:
        """Determine the destination directory for the given directory entry."""

        match entry.info:
            case _ if entry.name in self._IGNORED_FILES:
                return None

            case info if info.is_symlink():
                return None

            case info if info.is_dir() and (
                entry.name in self.destination_dirs
                or entry.name.endswith("download")
            ):
                return None

            case info if not (info.is_dir() or info.is_file()):
                return None

        match entry.suffix:
            case ".xmp":
                return "DEFER"

            case "":
                return self._get_extensionless_dst(entry)

            case ext if dst := self.extensions_map.get(ext):
                return dst

            case _:
                return self.FALLBACK_DIR_NAME

    def _get_extensionless_dst(self, file: Path) -> str:
        """Get the target directory for a file without an extension."""

        if self.signature_patterns is None:
            return self.FALLBACK_DIR_NAME

        try:
            with file.open("rb") as f:
                header = f.read(self.SIGNATURE_READ_SIZE)
        except OSError as e:
            logger.error(f"Could not open file '{file.name}': {e}")
            return self.FALLBACK_DIR_NAME

        if not header:
            return self.FALLBACK_DIR_NAME

        if not (match := self.signature_patterns.match(header)):
            return self.FALLBACK_DIR_NAME

        group_name = match.lastgroup or ""
        if not (file_ext := self._pattern_map.get(group_name)):
            return self.FALLBACK_DIR_NAME

        return self.extensions_map.get(file_ext, self.FALLBACK_DIR_NAME)

    def _move_file_and_sidecar(
        self, src: Path, dst_dir: Path
    ) -> tuple[Path | None, Path | None]:
        """Move `src` and, if it exists, its sidecar into `dst_dir`.

        A sidecar file is moved only if its main file is moved successfully.
        If moving the sidecar file fails, the process continues.

        :param src: the source file’s full path
        :param dst_dir: the destination’s full path
        :return: a tuple containing the final destination paths of the `src` and its sidecar
        """

        if (dst_path := self._try_move_into(src, dst_dir)) is None:
            return None, None

        src_sidecar = src.with_suffix(".xmp")
        dst_sidecar = dst_path.with_suffix(".xmp")
        try:
            # Overwrite existing sidecars in a destination dir
            return dst_path, src_sidecar.replace(dst_sidecar)
        except FileNotFoundError as e:
            logger.warning(f"Sidecar file not found for '{src.name}': {e}")
        except OSError as e:
            return dst_path, self._retry_move_into(src, dst_dir, e)
        return dst_path, None

    def _try_move_into(self, src: Path, dst_dir: Path) -> Path | None:
        """Attempt to move `src` into `dst_dir`.

        :param src: the source’s path
        :param dst_dir: the destination dir’s path
        :return: the final destination path if the move succeeds, `None` if a PermissionError is raised
        """

        try:
            return src.move_into(dst_dir)
        except FileExistsError:
            dst_generator = self._generate_unique_destination_path(dst_dir)
            max_attempts = self._MAX_PATH_COLLISION_RESOLUTIONS

            for dst_path in islice(dst_generator, max_attempts):
                try:
                    return src.move(dst_path)
                except FileExistsError:
                    continue

            msg = f"Failed to create a unique name for '{src.name}' after {max_attempts} attempts."
            logger.error(msg)
        except PermissionError as e:
            logger.error(f"Permission denied for '{src.name}': {e}")
        except OSError as e:
            # Retry if the OS temporarily locks the file
            return self._retry_move_into(src, dst_dir, e)
        return None

    @staticmethod
    def _retry_move_into(
        src: Path, dst_dir: Path, error: OSError
    ) -> Path | None:
        """Retry to move `src` into `dst_dir` after the caller raises an OSError.

        :param src: the source’s path
        :param dst_dir: the destination dir’s path
        :return: the final destination path if the move succeeds, `None` if all attempts fail
        """

        if error.errno not in FileOrganizer._TRANSIENT_ERRORS:
            logger.warning(f"Non-transient error: {error}")
            return None

        max_attempts = FileOrganizer._MAX_MOVE_RETRIES

        for n in range(max_attempts):
            msg = f"Retrying to move '{src.name}' (attempt {n + 1}/{max_attempts})"
            logger.info(msg)
            try:
                return src.move_into(dst_dir)
            except OSError:
                sleep(FileOrganizer._RETRY_DELAY)

        msg = f"Failed to move '{src.name}' after {max_attempts} retries: {error}"
        logger.warning(msg)
        return None

    @staticmethod
    def _generate_unique_destination_path(
        path: Path,
    ) -> Generator[Path, None, None]:
        stem = path.stem
        padding = len(str(FileOrganizer._MAX_PATH_COLLISION_RESOLUTIONS))

        for n in count(1):
            yield path.with_stem(f"{stem}_{n:0{padding}}")
