"""
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
"""

import json
import logging
import os
import re
from collections.abc import Generator
from configparser import ConfigParser
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType
from typing import Collection, Final, Mapping, TextIO

from .log_actions import LogActions

__all__ = "CONFIG_ENCODING", "MissingRequiredFieldsError", "OrganizerConfig"


CONFIG_ENCODING: Final = "utf-8"
"""File encoding used for configuration files."""


_REQUIRED_CONFIG_FIELDS: Final = frozenset({"dir_names", "ext_to_dir"})
"""Keys that must be defined in configuration files to prevent a
`MissingRequiredFieldsError`.
"""


_PYTHON_IDENTIFIER_RE: Final = re.compile(r"\W")
"""Regex used to sanitize file extensions into valid Python identifiers for
regex group names.
"""

_DEFAULT_MAX_MOVE_RETRIES: Final = int(os.getenv("FO_MAX_MOVE_RETRIES", 3))
_DEFAULT_RETRY_DELAY_SECONDS: Final = float(
    os.getenv("FO_RETRY_DELAY_SECONDS", 0.1)
)
_DEFAULT_MAX_COLLISION_ATTEMPTS: Final = int(
    os.getenv("FO_MAX_COLLISION_ATTEMPTS", 99)
)
_DEFAULT_DRY_RUN: Final = os.getenv("FO_DRY_RUN", "0") == "1"


logger = logging.getLogger(__name__)


class MissingRequiredFieldsError(Exception):
    """Raised when required fields in a configuration file are missing."""


class OrganizerConfig:
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

    @classmethod
    def from_ini(cls, config_path: Path) -> OrganizerConfig:
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

        ext_to_dir: dict[str, str] = {}
        for ext, key in parser["ext_to_dir"].items():
            if dir_name := parser["dir_names"].get(key):
                ext_to_dir[ext] = dir_name
            else:
                msg = f"{LogActions.CONFIG}: '{key}' not a key in `dir_names`, skipping."
                logger.warning(msg)

        ext_to_re = None
        if "ext_to_re" in parser:
            ext_to_re = parser["ext_to_re"]

        msg = f"{LogActions.CONFIG}: Loaded rules from '{config_path.name}'."
        logger.info(msg)
        return cls(dir_names, ext_to_dir, ext_to_re)

    # FIXME: Handle missing fields better
    @classmethod
    def from_json(cls, config_path: Path) -> OrganizerConfig:
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

        ext_to_dir: dict[str, str] = {}
        for dir_key, extensions in content["ext_to_dir"].items():
            for ext in extensions:
                ext_to_dir[ext] = content["dir_names"][dir_key]

        ext_to_re = None
        if "ext_to_re" in content:
            ext_to_re = content["ext_to_re"]

        msg = f"{LogActions.CONFIG}: Loaded rules from '{config_path.name}'."
        logger.info(msg)
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
                self.name_to_ext: Final = patterns[1]
            else:
                msg = f"{LogActions.INIT}: No valid binary signature regex "
                msg += "patterns provided."
                logger.warning(msg)
        else:
            logger.info(
                f"{LogActions.INIT}: No binary signature regex provided."
            )

        self.max_move_retries: Final = (
            max_move_retries
            if max_move_retries is not None
            else _DEFAULT_MAX_MOVE_RETRIES
        )
        self.retry_delay_seconds: Final = (
            retry_delay_seconds
            if retry_delay_seconds is not None
            else _DEFAULT_RETRY_DELAY_SECONDS
        )
        self.max_collision_attempts: Final = (
            max_collision_attempts
            if max_collision_attempts is not None
            else _DEFAULT_MAX_COLLISION_ATTEMPTS
        )
        self.dry_run: Final = dry_run or _DEFAULT_DRY_RUN

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

    @staticmethod
    def _compile_signature_re(
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
            if not (sanitized_ext := OrganizerConfig._sanitize_ext(ext)):
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
