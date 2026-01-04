"""Automated file organizer that supports sidecar files.

This module provides a rule-based file organizer that moves files into
specified directories based on their extensions or binary signatures. It also
handles `.xmp` sidecar files, ensuring they follow their parent files during
organization.

The organizer supports configuration via INI and JSON formats for destination
mappings.
"""

import errno
import json
import logging
import os
import re
from collections.abc import Collection, Generator, Mapping
from configparser import ConfigParser
from contextlib import contextmanager
from itertools import count, islice
from pathlib import Path
from time import sleep
from types import MappingProxyType
from typing import Final, TextIO

CONFIG_ENCODING: Final = "utf-8"
"""File encoding used for configuration files."""

_SEP: Final = os.sep
"""Platform-dependent file separator."""

_REQUIRED_CONFIG_FIELDS: Final = frozenset({"dir_names", "ext_to_dir"})
"""Keys that must be defined in configuration files to prevent a
`MissingRequiredFieldsError`.
"""

_IGNORED_NAMES: Final = frozenset({".DS_Store", ".localized"})
"""Files the organizer should skip entirely."""

_SIDECAR_EXTENSIONS: Final = frozenset({".aae", ".xmp"})
"""Extensions used by sidecar files."""

# TODO: Determine all transient errors
_TRANSIENT_ERRNO_CODES: Final = frozenset(
    {errno.EAGAIN, errno.EBUSY, errno.ETIMEDOUT}
)
"""OS error codes that trigger retry attempts because they are typically
temporary.
"""

_PYTHON_IDENTIFIER_RE: Final = re.compile(r"\W")
"""Regex used to sanitize file extensions into valid Python identifiers for
regex group names.
"""


logger = logging.getLogger(__name__)


class FileOrganizerError(Exception): ...


class MissingRequiredFieldsError(FileOrganizerError):
    """Raised when required fields in a configuration file are missing."""


# TODO: Add history
# TODO: Add undo functionality?
class FileOrganizer:
    """Manages file organization based on extension and binary signature maps.

    The organizer processes a root directory, identifying files based on their
    extension or, if extensionless, the first 32 bytes of their binary
    signatures.

    Attributes:
        SIGNATURE_READ_SIZE (int): The number of bytes read from an
            extensionless file’s header.
        DEFAULT_DIR_NAME (str): The fallback directory when a file isn’t mapped
          to a specified directory.

        dir_names (frozenset[str]): Directories the files will be
            moved into.
        ext_to_dir (types.MappingProxyType[str, str]): A mapping of file
            extensions (e.g., `.jpeg`) to their destination directory name.
        signatures_re (re.Pattern | None): A compiled regular expression
            to identify files based on binary signatures.
        max_move_retries (int): The maximum number of move retries when a file
            is locked by the OS.
        retry_delay_seconds (float): The number of seconds to wait before
            retrying to move a file.
        max_collision_attempts (int): The maximum number of file regeneration
            attempts when a filename collision arises.

        _name_to_ext (types.MappingProxyType[str, str]): A mapping
            of group names in the compiled regex pattern to their file
            extensions.
    """

    # Class constants

    SIGNATURE_READ_SIZE: Final = 32
    """The number of bytes read from the start of an extensionless file to
    check for binary signatures.
    """

    DEFAULT_DIR_NAME: Final = "Misc"
    """The default directory used when a directory or file does not match any
    rule.
    """

    # Class methods

    # FIXME: Handle KeyError
    @classmethod
    def from_ini(cls, config_path: Path) -> FileOrganizer:
        """Initialize the organizer using an INI configuration file.

        Args:
            config_path (pathlib.Path): The path to valid `.ini` configuration
                file.

        Returns:
            An instance of FileOrganizer configured with rules from the file.

        Raises:
            FileNotFoundError: If the provided path does not exist.
            MissingRequiredFieldsError: If the `dir_names` and
                `ext_to_dir` sections are missing.
        """

        parser = ConfigParser()
        with cls._read_validated_config(config_path) as f:
            parser.read_file(f)

        cls._validate_config_fields(parser.sections())

        dir_names = parser["dir_names"].values()

        ext_to_dir = {
            ext: parser["dir_names"][key]
            for ext, key in parser["ext_to_dir"].items()
        }

        ext_to_re = None
        if "ext_to_re" in parser:
            ext_to_re = parser["ext_to_re"]

        logger.info(f"CONFIG: Loaded rules from '{config_path.name}'.")

        return cls(dir_names, ext_to_dir, ext_to_re)

    # FIXME: Handle missing fields better
    @classmethod
    def from_json(cls, config_path: Path) -> FileOrganizer:
        """Initialize the organizer using an JSON configuration file.

        Args:
            config_path (pathlib.Path): The path to valid `.json` configuration
                file.

        Returns:
            An instance of FileOrganizer configured with rules from the file.

        Raises:
            FileNotFoundError: If the provided path does not exist.
            MissingRequiredFieldsError: If the `dir_names` and
                `ext_to_dir` fields are missing.
        """

        with cls._read_validated_config(config_path) as f:
            content = json.load(f)

        cls._validate_config_fields(content.keys())

        dir_names = content["dir_names"].values()

        ext_to_dir = {}
        for dir_key, extensions in content["ext_to_dir"].items():
            for ext in extensions:
                ext_to_dir[ext] = content["dir_names"][dir_key]

        ext_to_re = None
        if "ext_to_re" in content:
            ext_to_re = content["ext_to_re"]

        logger.info(f"CONFIG: Loaded rules from '{config_path.name}'.")

        return cls(dir_names, ext_to_dir, ext_to_re)

    # Magic methods

    def __init__(
        self,
        dir_names: Collection[str],
        ext_to_dir: Mapping[str, str],
        ext_to_re: Mapping[str, str] | None = None,
        *,
        max_move_retries: int | None = None,
        retry_delay_seconds: float | None = None,
        max_collision_attempts: int | None = None,
        dry_run: bool = False,
    ) -> None:
        unique_dir_names: Final = {
            self.DEFAULT_DIR_NAME,
            *dir_names,
        }

        validated_map: dict[str, str] = {}
        for ext, dst_dir in ext_to_dir.items():
            if not (sanitized_ext := self._sanitize_ext(ext)):
                msg = f"INIT: Sanitized '{ext}' is empty, skipping."
                logger.warning(msg)
                continue

            # `dst_dir` has be an existing entry in `unique_dst_dirs`
            if dst_dir in unique_dir_names:
                if sanitized_ext in validated_map:
                    msg = f"INIT: '{sanitized_ext}' already points to "
                    msg += f"'{validated_map[sanitized_ext]}'. Updating to "
                    msg += f"'{dst_dir}'."
                    logger.warning(msg)
                validated_map[sanitized_ext] = dst_dir
            else:
                msg = f"INIT: '{dst_dir}' not in `dir_names`, skipping '{ext}'."
                logger.warning(msg)

        self.dir_names: Final = frozenset(unique_dir_names)
        self.ext_to_dir: Final = MappingProxyType(validated_map)

        self.signatures_re = None
        if ext_to_re:
            if patterns := self._compile_signature_re(
                validated_map.keys(), ext_to_re
            ):
                self.signatures_re = patterns[0]
                self._name_to_ext: Final = patterns[1]
            else:
                msg = "INIT: No valid binary signature regex provided."
                logger.warning(msg)
        else:
            logger.info("INIT: No binary signature regex provided.")

        self.max_move_retries: Final = max_move_retries or int(
            os.getenv("FO_MAX_MOVE_RETRIES", 3)
        )
        self.retry_delay_seconds: Final = retry_delay_seconds or float(
            os.getenv("FO_RETRY_DELAY_SECONDS", 0.1)
        )
        self.max_collision_attempts: Final = max_collision_attempts or int(
            os.getenv("FO_MAX_COLLISION_ATTEMPTS", 99)
        )
        self._dry_run: Final = dry_run or os.getenv("FO_DRY_RUN", "0") == "1"

    # Public methods

    def organize(self, root_dir: Path) -> None:
        """Organize the contents of `root_dir`.

        Move directories and files into subdirectories based on their file
        extensions or binary signatures.

        Args:
            root_dir (Path): The directory to organize.

        Raises:
            NotADirectoryError: If `root_dir` is not a directory.
            PermissionError | OSError: If `root_dir` cannot be accessed.
        """

        if self._dry_run:
            logger.info("DRY-RUN: No changes will be made.")

        else:
            logger.info(f"STARTED: Organizing contents of '{root_dir.name}'.")
            logger.debug("Creating subdirectories.")

        self._create_dirs(root_dir)

        if not self._dry_run:
            logger.debug(f"Now processing entries in '{root_dir.name}'.")

        for entry in root_dir.iterdir():
            self._process_dir_entry(entry, root_dir)

        if not self._dry_run:
            logger.info(f"ORGANIZED: Contents of '{root_dir.name}'.")
            logger.debug("Now processing orphaned sidecar files.")

        sidecar_dst: Final = root_dir / self.DEFAULT_DIR_NAME
        for entry in root_dir.iterdir():
            if entry.name in _IGNORED_NAMES:
                logger.debug(f"SKIP: Ignored name '{entry.name}'.")
                continue

            info = entry.info
            if info.is_symlink():
                logger.debug(f"SKIP: Symlink '{entry.name}'.")
                continue

            if not info.is_file():
                logger.debug(f"SKIP: Not a regular file '{entry.name}'.")
                continue

            if entry.suffix.lower() in _SIDECAR_EXTENSIONS:
                self._move_to_dir(entry, sidecar_dst)

        if not self._dry_run:
            msg = f"ORGANIZED: Orphaned sidecar files in '{root_dir.name}'."
            logger.info(msg)

    # Private methods

    @classmethod
    @contextmanager
    def _read_validated_config(
        cls, config_path: Path
    ) -> Generator[TextIO, None, None]:
        """Read the contents of a configuration file.

        Yields:
            The contents of `config_path`.

        Raises:
            FileNotFoundError: If `config_path` is not a file.
        """

        try:
            with config_path.open("r", encoding=CONFIG_ENCODING) as f:
                yield f
        except (FileNotFoundError, IsADirectoryError) as e:
            raise FileNotFoundError from e

    @staticmethod
    def _validate_config_fields(fields: Collection[str]) -> None:
        """Validate the required fields of a config file.

        Raises:
            MissingRequiredFieldsError: If any of the required fields are
                missing.
        """

        if missing := _REQUIRED_CONFIG_FIELDS - frozenset(fields):
            msg = f"FAILED: Missing config fields: {', '.join(missing)}."
            raise MissingRequiredFieldsError(msg)

    @staticmethod
    def _sanitize_ext(ext: str) -> str:
        """Strip spaces and dots from `ext`, then lowercase it.

        Args:
            ext: The extension to sanitize.

        Returns:
            The sanitized file extension prepended with a single dot.

        Raises:
            TypeError: If `ext` is not a string.
        """

        if ext := ext.strip(" .").lower():
            return f".{ext}"
        return ""

    def _compile_signature_re(
        self,
        validated_ext: Collection[str],
        ext_to_re: Mapping[str, str],
    ) -> tuple[re.Pattern[bytes], MappingProxyType[str, str]] | None:
        """Compile multiple binary signature patterns into one optimized regex.

        This method takes raw signature strings, unescapes them, then wraps
        them into named capture groups. This allows the organizer to perform
        one single regex pass on a file’s header to determine its type.

        Args:
            validated_ext: A collection of extensions the organizer
                supports.
            ext_to_re: A mapping of extensions to their binary signature
                patterns.

        Returns:
            A tuple containing:
                - The compiled regex bytes pattern.
                - A mapping of regex group names to their respective file
                    extensions.
            or `None` if no valid patterns are provided.

        Note:
            Patterns are encoded using `latin-1` to safely map to raw bytes.
        """

        target_encoding: Final = "latin-1"

        groups: list[str] = []
        name_to_ext: dict[str, str] = {}

        # `ext` has to be an existing key in `normalized_map`
        for ext, raw_pattern in ext_to_re.items():
            if not (sanitized_ext := self._sanitize_ext(ext)):
                msg = f"INIT: Sanitized '{ext}' is empty, skipping."
                logger.warning(msg)
                continue

            elif sanitized_ext not in validated_ext:
                msg = f"INIT: '{sanitized_ext}' not in `extensions_map`, "
                msg += "skipping."
                logger.warning(msg)
                continue

            try:
                unescaped = raw_pattern.encode(CONFIG_ENCODING).decode(
                    "unicode_escape"
                )
                unescaped.encode(target_encoding)

                name = f"g_{_PYTHON_IDENTIFIER_RE.sub('_', sanitized_ext)}"
                name_to_ext[name] = sanitized_ext
                groups.append(f"(?P<{name}>(?>{unescaped}))")

            except (UnicodeError, re.error):
                msg = f"INIT: Invalid pattern '{raw_pattern}' for "
                msg += f"'{sanitized_ext}', skipping."
                logger.warning(msg)
                continue

        if not groups:
            return None

        combined: Final = "|".join(groups)
        compiled: Final = re.compile(
            combined.encode(target_encoding), re.NOFLAG
        )

        return compiled, MappingProxyType(name_to_ext)

    def _create_dirs(self, root_dir: Path) -> None:
        """Create the subdirectories listed in `dir_names`.

        Args:
            root_dir: The directory to organize.

        Raises:
            NotADirectoryError: If `root_dir` is not a directory.
            PermissionError | OSError: If `root_dir` cannot be accessed.
        """

        if not root_dir.info.is_dir():
            msg = f"FAILED: Not a directory '{root_dir.name}'."
            raise NotADirectoryError(msg)

        if self._dry_run:
            msg = f"DRY-RUN: Would create subdirectories in '{root_dir}{_SEP}'."
            logger.info(msg)
            return

        try:
            for dst in self.dir_names:
                (root_dir / dst).mkdir(parents=True, exist_ok=True)

        except (PermissionError, OSError):
            raise

        logger.info(f"CREATED: Subdirectories in '{root_dir.name}'.")

    def _process_dir_entry(self, entry: Path, root_dir: Path) -> None:
        """Process a directory entry and move it to the destination directory.

        Args:
            entry: The directory entry to be processed.
            root_dir: The source directory containing the entry.
        """

        if not (dst_dir_name := self._get_dst_dir_name(entry)):
            return

        dst_path = root_dir / dst_dir_name
        if entry.info.is_dir():
            d = self._move_to_dir(entry, dst_path)
            if not (self._dry_run or d is None):
                msg = f"MOVED: Directory '{d.name}' to '{dst_dir_name}{_SEP}'."
                logger.info(msg)

        else:
            p, s = self._move_file_and_sidecar(entry, dst_path)
            if not (self._dry_run or p is None):
                msg = f"MOVED: File '{p.name}' to '{dst_dir_name}{_SEP}'."
                logger.info(msg)
            if not (self._dry_run or s is None):
                msg = f"MOVED: Sidecar '{s.name}' to '{dst_dir_name}{_SEP}'."
                logger.info(msg)

    def _get_dst_dir_name(self, entry: Path) -> str | None:
        """Determine the destination directory for the given directory or file.

        Args:
            entry: The directory or file whose destination needs to be
                determined.

        Returns:
            `None` if the entry should not be moved, or the directory name based
                on its file extension or binary signature.
        """

        name: Final = entry.name
        ext: Final = entry.suffix.lower()
        info: Final = entry.info

        if name in _IGNORED_NAMES:
            return None

        if ext in _SIDECAR_EXTENSIONS:
            return None

        if info.is_symlink():
            return None

        if info.is_dir():
            if name in self.dir_names or name.endswith("download"):
                return None
            return self.DEFAULT_DIR_NAME

        if not info.is_file():
            return None

        if not ext:
            return self._get_dst_dir_name_by_signature(entry)

        if dst_dir_name := self.ext_to_dir.get(ext):
            return dst_dir_name

        return self.DEFAULT_DIR_NAME

    def _get_dst_dir_name_by_signature(self, file: Path) -> str:
        """Get the target directory for a file without an extension.

        Args:
            file: The extensionless file to check.

        Returns:
            The directory name based on `file`’s binary signature, the fallback
                directory name if its signature is not associated with a rule
        """

        if self.signatures_re is None:
            return self.DEFAULT_DIR_NAME

        try:
            with file.open("rb") as f:
                header: Final = f.read(self.SIGNATURE_READ_SIZE)

        except OSError as e:
            logger.error(f"FAILED: Opening '{file.name}': {e}")
            return self.DEFAULT_DIR_NAME

        if not header:
            return self.DEFAULT_DIR_NAME

        if not (match := self.signatures_re.match(header)):
            return self.DEFAULT_DIR_NAME

        group_name: Final = match.lastgroup or ""
        if not (ext := self._name_to_ext.get(group_name)):
            return self.DEFAULT_DIR_NAME

        msg = f"IDENTIFIED: '{file.name}' as '.{ext}' via binary signature."
        logger.info(msg)
        return self.ext_to_dir.get(ext, self.DEFAULT_DIR_NAME)

    def _move_file_and_sidecar(
        self, src: Path, dst_dir: Path
    ) -> tuple[Path | None, Path | None]:
        """Move `src` and, if it exists, its sidecar into `dst_dir`.

        A sidecar file is moved only if its main file is moved successfully.
        If moving the sidecar file fails, the process continues.

        Args:
            src: The source file’s full path.
            dst_dir: the destination directory’s path.

        Returns:
            A tuple containing:
                - The final destination path if moving `src` succeeds, `None`
                    otherwise.
                - The final destination path if moving `src`’s sidecar succeeds,
                    `None` otherwise.
        """

        if (dst := self._move_to_dir(src, dst_dir)) is None:
            return None, None

        src_sidecar = ext = None
        for ext in _SIDECAR_EXTENSIONS:
            src_sidecar = src.with_suffix(ext)
            if src_sidecar.info.exists():
                break

        if src_sidecar is None or ext is None:
            logger.debug(f"FAILED: '{src.name}' has no sidecar file.")
            return dst, None

        sidecar_dst: Final = dst.with_suffix(ext)

        if self._dry_run:
            msg = f"DRY_RUN: Would move '{src_sidecar.name}' to '{dst_dir}"
            msg += f"{_SEP}'.'"
            logger.info(msg)
            return dst, sidecar_dst

        try:
            # Overwrite existing sidecars in a destination
            return dst, src_sidecar.replace(sidecar_dst)

        except OSError as e:
            return dst, self._retry_move_to_dir(src, dst_dir, e)

    # TODO: Determine if pathlib.Path.move_into() is cross-drive safe in Windows
    def _move_to_dir(self, src: Path, dst_dir: Path) -> Path | None:
        """Attempt to move `src` into `dst_dir`.

        Args:
            src: The full path of a file or directory to move.
            dst_dir: The destination directory’s path

        Returns:
            The final destination path if moving `src` succeeds, `None`
                otherwise.
        """

        if self._dry_run:
            msg = f"DRY-RUN: Would move '{src.name}' to '{dst_dir}{_SEP}'."
            logger.info(msg)
            return dst_dir / src.name

        try:
            return src.move_into(dst_dir)

        except FileExistsError:
            path_generator: Final = self._generate_unique_destination_path(
                dst_dir / src.name
            )

            for dst_path in islice(path_generator, self.max_collision_attempts):
                try:
                    return src.move(dst_path)

                except FileExistsError:
                    continue

            msg = f"FAILED: Generating a unique path for '{src.name}' after "
            msg += f"{self.max_collision_attempts} attempts."
            logger.error(msg)

        except PermissionError as e:
            logger.error(f"FAILED: Permission denied for '{src.name}': {e}")

        except OSError as e:
            # Retry if the OS temporarily locks the file.
            return self._retry_move_to_dir(src, dst_dir, e)

        return None

    def _retry_move_to_dir(
        self, src: Path, dst_dir: Path, error: OSError
    ) -> Path | None:
        """Retry moving `src` into `dst_dir` after the caller raises an OSError.

        Args:
            src: The full path of a file or directory to move.
            dst_dir: The destination directory’s path.

        Returns:
            `None` if the OSError in the calling function is not transient.
            The final destination path if moving `src` succeeds, `None` if
                all attempts fail.
        """

        if error.errno not in _TRANSIENT_ERRNO_CODES:
            logger.error(f"FAILED: Non-transient error: {error}")
            return None

        for n in range(self.max_move_retries):
            delay = self.retry_delay_seconds * (2**n)

            msg = f"RETRY [{n + 1}/{self.max_move_retries}]: Moving "
            msg += f"'{src.name}' in {delay:.2f} s."
            logger.info(msg)
            try:
                return src.move_into(dst_dir)

            except OSError:
                sleep(delay)

        msg = f"FAILED: Moving '{src.name}' after {self.max_move_retries} "
        msg += f"retries: {error}"
        logger.error(msg)
        return None

    def _generate_unique_destination_path(
        self,
        path: Path,
    ) -> Generator[Path, None, None]:
        """Generate a path with a counter appended to its stem.

        Args:
            path: A destination path that encounters a name collision.

        Yields:
            A path with a number appended to its stem.
        """

        stem: Final = path.stem
        padding: Final = len(str(self.max_collision_attempts))

        for n in count(1):
            yield path.with_stem(f"{stem}_{n:0{padding}}")
