"""Automated file organizer that supports sidecar files.

This module provides a rule-based file organizer that moves files into
specified directories based on their extensions or binary signatures. The
organizer supports configuration via INI and JSON formats for destination
mappings.

Example INI schema:
    [dir_names]
    # Key = Directory Name
    images = Images
    programming = Programming
    python = Programming/Python

    [ext_to_dir]
    # File Extension = Key from [dir_names]
    jpg = images
    png = images
    py = python
    pyw = python

    [ext_to_re]
    # File Extension = Regex Pattern
    png = \x89PNG
    py = #!/.+?python

Example JSON schema:
{
    "dir_names": {
        "images": "Images",
        "programming": "Programming",
        "python": "Programming/Python"
    },
    "ext_to_dir": {
        "images": [".jpg", ".png"],
        "python": [".py", ".pyw"]
    },
    "ext_to_re": {
        "png": "\\x89PNG",
        "py": "#!/.+?python"
    }
}

It also handles `.aae` and `.xmp` sidecar files, ensuring they follow their
parent files during organization.
"""

import errno
import json
import logging
import os
import re
from collections.abc import Collection, Generator, Mapping
from configparser import ConfigParser
from contextlib import contextmanager
from enum import StrEnum, unique
from itertools import count, islice
from pathlib import Path
from time import sleep
from types import MappingProxyType
from typing import Final, TextIO

CONFIG_ENCODING: Final = "utf-8"
"""File encoding used for configuration files."""

FILE_SEP: Final = os.sep
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


@unique
class LogActions(StrEnum):
    CONFIG = "CONFIG"
    INIT = "INIT"
    DRY_RUN = "DRY-RUN"

    STARTED = "STARTED"
    FINISHED = "FINISHED"

    CREATED = "CREATED"
    MOVED = "MOVED"
    IDENTIFIED = "IDENTIFIED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"

    RETRYING = "RETRYING"
    WAITING = "WAITING"


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

        dir_names (frozenset[str]): A readonly set of valid unique directory
            names directories and files will be moved into.
        ext_to_dir (types.MappingProxyType[str, str]): A readonly mapping of
            sanitized file extensions (e.g., `.jpg`) to their destination
            directory name.
        signatures_re (re.Pattern | None): A compiled regular expression
            to identify files based on their binary signatures.
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
        """Initialize the FileOrganizer with specific configurations.

        This constructor sanitizes the provided extension mappings and compiles
        binary signature patterns if provided. Configuration values (retries,
        delays) prioritize explicit arguments, falling back to environment
        variables, and finally hardcoded defaults.

        Args:
            dir_names: A collection of directory names to move files into.
            ext_to_dir: A mapping of file extensions to their destination
                directory names.
            ext_to_re: A mapping of file extensions to their binary signature
                regex patterns.
            max_move_retries: The maximum number of move retries when a file
                is locked by the OS. Defaults to `FO_MAX_MOVE_RETIRES` or 3.
            retry_delay_seconds: The number of seconds to wait before retrying
                to move a file. Defaults to `FO_RETRY_DELAY_SECONDS` or 0.1
                seconds.
            max_collision_attempts: The maximum number of file regeneration
                attempts when a filename collision arises. Defaults to
                `FO_MAX_COLLISION_ATTEMPTS` or 99.
            dry_run: If `True`, no actual file operations are performed.
                Defaults to `FO_DRY_RUN` or `False`.
        """

        unique_dir_names: Final = {
            self.DEFAULT_DIR_NAME,
            *dir_names,
        }

        validated_map: dict[str, str] = {}
        for ext, dst_dir in ext_to_dir.items():
            if not (sanitized_ext := self._sanitize_ext(ext)):
                msg = f"{LogActions.INIT}: Sanitized '{ext}' is empty, "
                msg += "skipping."
                logger.warning(msg)
                continue

            # `dst_dir` has be an existing entry in `unique_dst_dirs`
            if dst_dir in unique_dir_names:
                if sanitized_ext in validated_map:
                    msg = f"{LogActions.INIT}: '{sanitized_ext}' already "
                    msg += f"points to '{validated_map[sanitized_ext]}'. "
                    msg += f"Updating to '{dst_dir}'."
                    logger.warning(msg)
                validated_map[sanitized_ext] = dst_dir
            else:
                msg = f"{LogActions.INIT}: '{dst_dir}' not in `dir_names`, "
                msg += "skipping."
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
                msg = f"{LogActions.INIT}: No valid binary signature regex "
                msg += "patterns provided."
                logger.warning(msg)
        else:
            logger.info(
                f"{LogActions.INIT}: No binary signature regex provided."
            )

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
            logger.info(f"{LogActions.DRY_RUN}: No changes will be made.")

        self._create_dirs(root_dir)

        logger.debug(
            f"{LogActions.STARTED}: Processing entries in '{root_dir.name}'."
        )

        for entry in root_dir.iterdir():
            self._process_dir_entry(entry, root_dir)

        logger.debug(
            f"{LogActions.STARTED}: Processing orphaned sidecar files."
        )
        sidecar_dst: Final = root_dir / self.DEFAULT_DIR_NAME
        for entry in root_dir.iterdir():
            if entry.name in _IGNORED_NAMES:
                msg = f"{LogActions.SKIPPED}: Ignored name '{entry.name}'."
                logger.debug(msg)
                continue

            info = entry.info
            if info.is_symlink():
                logger.debug(f"{LogActions.SKIPPED}: Symlink '{entry.name}'.")
                continue

            if not info.is_file():
                msg = f"{LogActions.SKIPPED}: Not a regular file "
                msg += f"'{entry.name}'."
                logger.debug(msg)
                continue

            if entry.suffix.lower() in _SIDECAR_EXTENSIONS:
                self._move_to_dir(entry, sidecar_dst)

        msg = f"{LogActions.FINISHED}: Processing items in '{root_dir.name}"
        msg += f"{FILE_SEP}'."
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
            msg = f"{LogActions.FAILED}: Missing config fields: "
            msg += f"{', '.join(missing)}."
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
                - A mapping of regex group names (e.g. g_jpg) to their
                    respective file extensions.
            Returns `None` if no valid patterns are provided or if all provided
                patterns fail to compile.

        Note:
            Patterns are encoded using `latin-1` to safely map to raw bytes.
        """

        target_encoding: Final = "latin-1"

        groups: list[str] = []
        name_to_ext: dict[str, str] = {}

        # `ext` has to be an existing key in `normalized_map`
        for ext, raw_pattern in ext_to_re.items():
            if not (sanitized_ext := self._sanitize_ext(ext)):
                msg = f"{LogActions.INIT}: Sanitized '{ext}' is empty, "
                msg += "skipping."
                logger.warning(msg)
                continue

            elif sanitized_ext not in validated_ext:
                msg = f"{LogActions.INIT}: '{sanitized_ext}' not in "
                msg += "`extensions_map`, skipping."
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
                msg = f"{LogActions.INIT}: Invalid pattern '{raw_pattern}' for "
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
            msg = f"{LogActions.FAILED}: Not a directory '{root_dir.name}'."
            raise NotADirectoryError(msg)

        if self._dry_run:
            msg = f"{LogActions.DRY_RUN}: Would create subdirectories in "
            msg += f"'{root_dir.name}{FILE_SEP}'."
            logger.info(msg)
            return

        try:
            for dst in self.dir_names:
                (root_dir / dst).mkdir(parents=True, exist_ok=True)

        except (PermissionError, OSError):
            raise

        logger.info(
            f"{LogActions.CREATED}: Subdirectories in '{root_dir.name}'."
        )

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
                msg = f"{LogActions.MOVED}: Directory '{d.name}' to "
                msg += f"'{dst_dir_name}{FILE_SEP}'."
                logger.info(msg)

        else:
            p, s = self._move_file_and_sidecar(entry, dst_path)
            if not (self._dry_run or p is None):
                msg = f"{LogActions.MOVED}: File '{p.name}' to '{dst_dir_name}"
                msg += f"{FILE_SEP}'."
                logger.info(msg)
            if not (self._dry_run or s is None):
                msg = f"{LogActions.MOVED}: Sidecar '{s.name}' to "
                msg += f"'{dst_dir_name}{FILE_SEP}'."
                logger.info(msg)

    def _get_dst_dir_name(self, entry: Path) -> str | None:
        """Determine the destination directory for the given directory or file.

        This method skips ignored names, sidecar files, symlinks, and certain
        directories and non-regular files. It uses file extensions or binary
        signatures to map files to their target subdirectory names.

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
            logger.debug(f"{LogActions.SKIPPED}: Ignored name '{name}'.")
            return None

        if ext in _SIDECAR_EXTENSIONS:
            logger.debug(f"{LogActions.SKIPPED}: Sidecar file '{ext}'.")
            return None

        if info.is_symlink():
            logger.debug(f"{LogActions.SKIPPED}: Symlink '{info}'.")
            return None

        if info.is_dir():
            if name in self.dir_names:
                msg = f"{LogActions.SKIPPED}: Target directory '{name}"
                msg += f"{FILE_SEP}'."
                logger.debug(msg)
                return None
            if name.endswith("download"):
                msg = f"{LogActions.SKIPPED}: In-progress download '{name}'."
                logger.debug(msg)
                return None
            return self.DEFAULT_DIR_NAME

        if not info.is_file():
            logger.debug(f"{LogActions.SKIPPED}: Not a regular file '{info}'.")
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
            msg = f"{LogActions.SKIPPED}: No regex signatures defined, "
            msg += "returning default."
            logger.debug(msg)
            return self.DEFAULT_DIR_NAME

        try:
            with file.open("rb") as f:
                header: Final = f.read(self.SIGNATURE_READ_SIZE)

        except OSError as e:
            logger.error(f"{LogActions.FAILED}: Opening '{file.name}': {e}")
            return self.DEFAULT_DIR_NAME

        if not header:
            msg = f"{LogActions.SKIPPED}: Empty file '{file.name}', returning "
            msg += "default."
            logger.debug(msg)
            return self.DEFAULT_DIR_NAME

        if not (match := self.signatures_re.match(header)):
            msg = f"{LogActions.SKIPPED}: No matching signature, returning "
            msg += "default."
            logger.debug(msg)
            return self.DEFAULT_DIR_NAME

        msg = "Regex match found but `lastgroup` is `None`."
        assert match.lastgroup is not None, msg
        ext = self._name_to_ext[match.lastgroup]

        msg = f"{LogActions.IDENTIFIED}: '{file.name}' as '.{ext}' via binary "
        msg += "signature."
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
            logger.debug(
                f"{LogActions.SKIPPED}: '{src.name}' has no sidecar file."
            )
            return dst, None

        sidecar_dst: Final = dst.with_suffix(ext)

        if self._dry_run:
            msg = f"{LogActions.DRY_RUN}: Would move '{src_sidecar.name}' to "
            msg += f"'{dst_dir}{FILE_SEP}'.'"
            logger.info(msg)
            return dst, sidecar_dst

        try:
            # Overwrite existing sidecars in a destination
            return dst, src_sidecar.replace(sidecar_dst)

        except OSError as e:
            return dst, self._retry_move_to_dir(src, dst_dir, e)

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
            msg = f"{LogActions.DRY_RUN}: Would move '{src.name}' to '{dst_dir}"
            msg += f"{FILE_SEP}'."
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

            msg = f"{LogActions.FAILED}: Generating a unique path for "
            msg += f"'{src.name}' after {self.max_collision_attempts} attempts."
            logger.error(msg)

        except PermissionError as e:
            logger.error(
                f"{LogActions.FAILED}: Permission denied for '{src.name}': {e}"
            )

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
            logger.error(f"{LogActions.FAILED}: Non-transient error: {error}")
            return None

        for n in range(self.max_move_retries):
            delay = self.retry_delay_seconds * (2**n)

            msg = f"{LogActions.RETRYING} [{n + 1}/{self.max_move_retries}]: "
            msg += f"Moving '{src.name}' in {delay:.2f} s."
            logger.info(msg)
            sleep(delay)
            try:
                return src.move_into(dst_dir)

            except OSError:
                continue

        msg = f"{LogActions.FAILED}: Moving '{src.name}' after "
        msg += f"{self.max_move_retries} retries: {error}"
        logger.error(msg)
        return None

    def _generate_unique_destination_path(
        self,
        path: Path,
    ) -> Generator[Path, None, None]:
        """Generate paths with an incrementing counter appended to their stem.

        Args:
            path: A path that encounters a name collision.

        Yields:
            A path with an incremented counter appended to its stem.
        """

        stem: Final = path.stem
        padding: Final = len(str(self.max_collision_attempts))

        for n in count(1):
            yield path.with_stem(f"{stem}_{n:0{padding}}")
