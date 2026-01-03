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

_REQUIRED_CONFIG_KEYS: Final = frozenset({"destination_dirs", "extensions_map"})
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


# TODO: Add dry-run mode
# TODO: Add history
# TODO: Better log messages
# TODO: Add undo functionality?
class FileOrganizer:
    """Manages file organization based on extension and binary signature maps.

    The organizer processes a root directory, identifying files based on their
    extension or, if extensionless, the first 32 bytes of their binary
    signatures.

    Attributes:
        SIGNATURE_READ_SIZE (int): The number of bytes read from an
            extensionless file’s header.
        FALLBACK_DIR_NAME (str): The fallback directory when a file isn’t mapped
          to a specified directory.

        destination_dir_names (frozenset[str]): Directories the files will be
            moved into.
        extension_to_dir (types.MappingProxyType[str, str]): A mapping of file
            extensions (e.g., `.jpeg`) to their destination directory name.
        signature_pattern_re (re.Pattern | None): A compiled regular expression
            to identify files based on binary signatures.
        max_move_attempts (int): The maximum number of move retries when a file
            is locked by the OS.
        retry_delay_seconds (float): The number of seconds to wait before
            retrying to move a file.
        max_collision_attempts (int): The maximum number of file regeneration
            attempts when a filename collision arises.

        _group_name_to_extension (types.MappingProxyType[str, str]): A mapping
            of group names in the compiled regex pattern to their file
            extensions.
    """

    # Class constants

    SIGNATURE_READ_SIZE: Final = 32
    """The number of bytes read from the start of an extensionless file to
    check for binary signatures.
    """

    FALLBACK_DIR_NAME: Final = "Misc"
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
            MissingRequiredFieldsError: If the `destination_dir_names` and
                `extension_to_dir` sections are missing.
        """

        parser = ConfigParser()
        with cls._read_validated_config(config_path) as f:
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

    # FIXME: Handle missing keys better
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
            MissingRequiredFieldsError: If the `destination_dir_names` and
                `extension_to_dir` keys are missing.
        """

        with cls._read_validated_config(config_path) as f:
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
        destination_dir_names: Collection[str],
        extension_to_dir: Mapping[str, str],
        signature_patterns_map: Mapping[str, str] | None = None,
        *,
        max_collision_attempts: int = 99,
        max_move_attempts: int = 3,
        retry_delay_seconds: float = 0.1,
    ) -> None:
        unique_dst_dirs: Final = {
            self.FALLBACK_DIR_NAME,
            *destination_dir_names,
        }

        validated_map: dict[str, str] = {}
        for ext, dst in extension_to_dir.items():
            if not (sanitized_ext := self._sanitize_file_extension(ext)):
                msg = f"Sanitized file extension '{ext}' is empty, skipping."
                logger.warning(msg)
                continue

            # `dst` has be an existing entry in `unique_dst_dirs`
            if dst in unique_dst_dirs:
                if sanitized_ext in validated_map:
                    msg = f"{sanitized_ext} already exists in {validated_map}, "
                    msg += "updating value."
                    logger.warning(msg)
                validated_map[sanitized_ext] = dst
            else:
                logger.warning(
                    f"{dst} not in `destination_dir_names`, ignoring."
                )

        self.destination_dir_names: Final = frozenset(unique_dst_dirs)
        self.extension_to_dir: Final = MappingProxyType(validated_map)

        self.signature_pattern_re = None
        if signature_patterns_map:
            if patterns := self._compile_signature_patterns(
                validated_map.keys(), signature_patterns_map
            ):
                self.signature_pattern_re = patterns[0]
                self._group_name_to_extension: Final = patterns[1]

        self.max_move_attempts: Final = max_move_attempts
        self.retry_delay_seconds: Final = retry_delay_seconds
        self.max_collision_attempts: Final = max_collision_attempts

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

        self._create_destination_dirs(root_dir)

        for entry in root_dir.iterdir():
            self._process_dir_entry(entry, root_dir)

        sidecar_dst: Final = root_dir / self.FALLBACK_DIR_NAME
        for entry in root_dir.iterdir():
            if entry.name in _IGNORED_NAMES:
                continue

            info = entry.info
            if info.is_symlink():
                continue

            elif not info.is_file():
                continue

            elif entry.suffix.lower() in _SIDECAR_EXTENSIONS:
                self._try_move_into(entry, sidecar_dst)

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
    def _validate_config_required_fields(keys: Collection[str]) -> None:
        """Validate the required fields of a config file.

        Raises:
            MissingRequiredFieldsError: If any of the required fields are
                missing.
        """

        if missing := _REQUIRED_CONFIG_KEYS - frozenset(keys):
            msg = f"Missing required sections: {', '.join(missing)}"
            raise MissingRequiredFieldsError(msg)

    @staticmethod
    def _sanitize_file_extension(ext: str) -> str:
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

    def _compile_signature_patterns(
        self,
        validated_extensions: Collection[str],
        signature_patterns: Mapping[str, str],
    ) -> tuple[re.Pattern[bytes], MappingProxyType[str, str]] | None:
        """Compile multiple binary signature patterns into one optimized regex.

        This method takes raw signature strings, unescapes them, then wraps
        them into named capture groups. This allows the organizer to perform
        one single regex pass on a file’s header to determine its type.

        Args:
            validated_extensions: A collection of extensions the organizer
                supports.
            signature_patterns: A mapping of extensions to their binary
                signature patterns.

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
        name_map: dict[str, str] = {}

        # `ext` has to be an existing key in `normalized_map`
        for ext, raw_pattern in signature_patterns.items():
            if not (sanitized_ext := self._sanitize_file_extension(ext)):
                msg = f"Sanitized file extension '{ext}' is empty, skipping."
                logger.warning(msg)
                continue

            elif sanitized_ext not in validated_extensions:
                msg = f"{sanitized_ext} not in `extensions_map`, ignoring."
                logger.warning(msg)
                continue

            try:
                unescaped = raw_pattern.encode(CONFIG_ENCODING).decode(
                    "unicode_escape"
                )
                unescaped.encode(target_encoding)

                group_name = (
                    f"g_{_PYTHON_IDENTIFIER_RE.sub('_', sanitized_ext)}"
                )
                name_map[group_name] = sanitized_ext
                groups.append(f"(?P<{group_name}>(?>{unescaped}))")

            except (UnicodeError, re.error):
                msg = f"Invalid pattern '{raw_pattern}' for '{sanitized_ext}', "
                msg += "skipping."
                logger.warning(msg)
                continue

        if not groups:
            return None

        combined: Final = "|".join(groups)
        compiled: Final = re.compile(
            combined.encode(target_encoding), re.NOFLAG
        )

        return compiled, MappingProxyType(name_map)

    def _create_destination_dirs(self, root_dir: Path) -> None:
        """Create the subdirectories listed in `destination_dir_names`.

        Args:
            root_dir: The directory to organize.

        Raises:
            NotADirectoryError: If `root_dir` is not a directory.
            PermissionError | OSError: If `root_dir` cannot be accessed.
        """

        if not root_dir.info.is_dir():
            raise NotADirectoryError(f"Not a directory: '{root_dir.name}'")

        try:
            for dst in self.destination_dir_names:
                (root_dir / dst).mkdir(parents=True, exist_ok=True)

        except (PermissionError, OSError):
            raise

    def _process_dir_entry(self, entry: Path, root_dir: Path) -> None:
        """Process a directory entry and move it to the destination directory.

        Args:
            entry: The directory entry to be processed.
            root_dir: The source directory containing the entry.
        """

        if not (dst_dir := self._determine_dst(entry)):
            return

        dst_path = root_dir / dst_dir
        if entry.info.is_dir():
            self._try_move_into(entry, dst_path)

        else:
            self._move_file_and_sidecar(entry, dst_path)

    def _determine_dst(self, entry: Path) -> str | None:
        """Determine the destination directory for the given directory or file.

        Args:
            entry: The directory or file whose destination needs to be
                determined.

        Returns:
            `None` if the entry should not be moved, or the directory name based
                on its file extension or binary signature.
        """

        name: Final = entry.name
        if name in _IGNORED_NAMES:
            return None

        ext: Final = entry.suffix.lower()
        if ext in _SIDECAR_EXTENSIONS:
            return None

        info: Final = entry.info
        if info.is_symlink():
            return None

        elif info.is_dir():
            if name in self.destination_dir_names or name.endswith("download"):
                return None
            return self.FALLBACK_DIR_NAME

        elif not info.is_file():
            return None

        elif not entry.suffix:
            return self._get_extensionless_dst(entry)

        elif dst := self.extension_to_dir.get(ext):
            return dst

        return self.FALLBACK_DIR_NAME

    def _get_extensionless_dst(self, file: Path) -> str:
        """Get the target directory for a file without an extension.

        Args:
            file: The extensionless file to check.

        Returns:
            The directory name based on `file`’s binary signature, the fallback
                directory name if its signature is not associated with a rule
        """

        if self.signature_pattern_re is None:
            return self.FALLBACK_DIR_NAME

        try:
            with file.open("rb") as f:
                header: Final = f.read(self.SIGNATURE_READ_SIZE)

        except OSError as e:
            logger.error(f"Could not open file '{file.name}': {e}")
            return self.FALLBACK_DIR_NAME

        if not header:
            return self.FALLBACK_DIR_NAME

        elif not (match := self.signature_pattern_re.match(header)):
            return self.FALLBACK_DIR_NAME

        group_name: Final = match.lastgroup or ""
        if not (file_ext := self._group_name_to_extension.get(group_name)):
            return self.FALLBACK_DIR_NAME
        return self.extension_to_dir.get(file_ext, self.FALLBACK_DIR_NAME)

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

        if (dst_path := self._try_move_into(src, dst_dir)) is None:
            return None, None

        src_sidecar = ext = None
        for ext in _SIDECAR_EXTENSIONS:
            src_sidecar = src.with_suffix(ext)
            if src_sidecar.info.exists():
                break

        if src_sidecar is None or ext is None:
            logger.info(f"'{src.name}' does not have a sidecar file.")
            return dst_path, None

        dst_sidecar: Final = dst_path.with_suffix(ext)
        try:
            # Overwrite existing sidecars in a destination
            return dst_path, src_sidecar.replace(dst_sidecar)

        except OSError as e:
            return dst_path, self._retry_move_into(src, dst_dir, e)

    # TODO: Determine if pathlib.Path.move_into() is cross-drive safe in Windows
    def _try_move_into(self, src: Path, dst_dir: Path) -> Path | None:
        """Attempt to move `src` into `dst_dir`.

        Args:
            src: The full path of a file or directory to move.
            dst_dir: The destination directory’s path

        Returns:
            The final destination path if moving `src` succeeds, `None`
                otherwise.
        """

        try:
            return src.move_into(dst_dir)

        except FileExistsError:
            dst_generator: Final = self._generate_unique_destination_path(
                dst_dir / src.name
            )

            for dst_path in islice(dst_generator, self.max_collision_attempts):
                try:
                    return src.move(dst_path)

                except FileExistsError:
                    continue

            msg = f"Failed to create a unique name for '{src.name}' "
            msg += f"after {self.max_collision_attempts} attempts."
            logger.error(msg)

        except PermissionError as e:
            logger.error(f"Permission denied for '{src.name}': {e}")

        except OSError as e:
            # Retry if the OS temporarily locks the file.
            return self._retry_move_into(src, dst_dir, e)

        return None

    def _retry_move_into(
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
            logger.warning(f"Non-transient error: {error}")
            return None

        for n in range(self.max_move_attempts):
            delay = self.retry_delay_seconds * (2**n)

            msg = f"Retrying to move '{src.name}' in {delay:.2f}s "
            msg += f"(attempt {n + 1}/{self.max_move_attempts})"
            logger.info(msg)
            try:
                return src.move_into(dst_dir)

            except OSError:
                sleep(delay)

        msg = f"Failed to move '{src.name}' after {self.max_move_attempts} "
        msg += f"retries: {error}"
        logger.warning(msg)
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
