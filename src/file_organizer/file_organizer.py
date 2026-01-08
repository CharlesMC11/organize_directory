import errno
import logging
import os
from collections.abc import Generator
from itertools import count, islice
from pathlib import Path
from time import sleep
from typing import Final

from .log_actions import LogActions
from .organizer_config import OrganizerConfig

__all__ = ("FILE_SEP", "FileOrganizer")


FILE_SEP: Final = os.sep
"""Platform-dependent file separator."""

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

logger = logging.getLogger(__name__)


# TODO: Add history
# TODO: Add undo functionality?
class FileOrganizer:
    """Manages file organization based on extension and binary signature maps.

    The organizer processes a root directory, identifying files based on their
    extension or, if extensionless, the first 32 bytes of their binary
    signatures.

    Attributes:
        config (OrganizerConfig): The organizer configuration.
    """

    # Magic methods

    def __init__(self, config: OrganizerConfig) -> None:
        self.config: Final = config

    # Public methods

    def organize(self, root_dir: Path) -> None:
        """Organize the contents of `root_dir`.

        Move directories and files into subdirectories based on their file
        extensions or binary signatures.

        Args:
            root_dir: The directory to organize.

        Raises:
            NotADirectoryError: If `root_dir` is not a directory.
            PermissionError | OSError: If `root_dir` cannot be accessed.
        """

        if self.config.dry_run:
            logger.info(f"{LogActions.DRY_RUN}: No changes will be made.")

        if not root_dir.is_dir():
            msg = f"{LogActions.FAILED}: Not a directory '{root_dir.name}'."
            raise NotADirectoryError(msg)

        self._create_dirs(root_dir)

        msg = f"{LogActions.STARTED}: Processing entries in '{root_dir.name}"
        msg += f"{FILE_SEP}'."
        logger.info(msg)

        for entry in root_dir.iterdir():
            self._process_dir_entry(entry, root_dir)

        msg = f"{LogActions.STARTED}: Processing orphaned sidecar files."
        logger.debug(msg)

        sidecar_dst: Final = root_dir / self.config.DEFAULT_DIR_NAME
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

    def _create_dirs(self, root_dir: Path) -> None:
        """Create the subdirectories listed in `dir_names`.

        Args:
            root_dir: The directory to organize.

        Raises:
            NotADirectoryError: If `root_dir` is not a directory.
            PermissionError | OSError: If `root_dir` cannot be accessed.
        """

        if self.config.dry_run:
            msg = f"{LogActions.DRY_RUN}: Would create subdirectories in "
            msg += f"'{root_dir.name}{FILE_SEP}'."
            logger.info(msg)
            return

        try:
            for dst in self.config.dir_names:
                (root_dir / dst).mkdir(parents=True, exist_ok=True)

        except (PermissionError, OSError):
            raise

        msg = f"{LogActions.CREATED}: Subdirectories in '{root_dir.name}'."
        logger.info(msg)

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
            if not (self.config.dry_run or d is None):
                msg = f"{LogActions.MOVED}: Directory '{d.name}' to "
                msg += f"'{dst_dir_name}{FILE_SEP}'."
                logger.info(msg)

        else:
            p, s = self._move_file_and_sidecar(entry, dst_path)
            if not (self.config.dry_run or p is None):
                msg = f"{LogActions.MOVED}: File '{p.name}' to '{dst_dir_name}"
                msg += f"{FILE_SEP}'."
                logger.info(msg)
            if not (self.config.dry_run or s is None):
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
            if name in self.config.dir_names:
                msg = f"{LogActions.SKIPPED}: Target directory '{name}"
                msg += f"{FILE_SEP}'."
                logger.debug(msg)
                return None
            if name.endswith("download"):
                msg = f"{LogActions.SKIPPED}: In-progress download '{name}'."
                logger.debug(msg)
                return None
            return self.config.DEFAULT_DIR_NAME

        if not info.is_file():
            logger.debug(f"{LogActions.SKIPPED}: Not a regular file '{info}'.")
            return None

        if not ext:
            return self._get_dst_dir_name_by_signature(entry)

        if dst_dir_name := self.config.ext_to_dir.get(ext):
            return dst_dir_name

        return self.config.DEFAULT_DIR_NAME

    def _get_dst_dir_name_by_signature(self, file: Path) -> str:
        """Get the target directory for a file without an extension.

        Args:
            file: The extensionless file to check.

        Returns:
            The directory name based on `file`’s binary signature, the fallback
                directory name if its signature is not associated with a rule
        """

        if self.config.signatures_re is None:
            msg = f"{LogActions.SKIPPED}: No regex signatures defined, "
            msg += "returning default."
            logger.debug(msg)
            return self.config.DEFAULT_DIR_NAME

        try:
            with file.open("rb") as f:
                header: Final = f.read(self.config.SIGNATURE_READ_SIZE)

        except OSError as e:
            logger.error(f"{LogActions.FAILED}: Opening '{file.name}': {e}")
            return self.config.DEFAULT_DIR_NAME

        if not header:
            msg = f"{LogActions.SKIPPED}: Empty file '{file.name}', returning "
            msg += "default."
            logger.debug(msg)
            return self.config.DEFAULT_DIR_NAME

        if not (match := self.config.signatures_re.match(header)):
            msg = f"{LogActions.SKIPPED}: No matching signature, returning "
            msg += "default."
            logger.debug(msg)
            return self.config.DEFAULT_DIR_NAME

        msg = "Regex match found but `lastgroup` is `None`."
        assert match.lastgroup is not None, msg
        ext = self.config.name_to_ext[match.lastgroup]

        msg = f"{LogActions.IDENTIFIED}: '{file.name}' as '{ext}' via binary "
        msg += "signature."
        logger.info(msg)
        return self.config.ext_to_dir.get(ext, self.config.DEFAULT_DIR_NAME)

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

        if self.config.dry_run:
            msg = f"{LogActions.DRY_RUN}: Would move '{src_sidecar.name}' to "
            msg += f"'{dst_dir.name}{FILE_SEP}'."
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

        if self.config.dry_run:
            msg = f"{LogActions.DRY_RUN}: Would move '{src.name}' to "
            msg += f"{dst_dir.name}{FILE_SEP}'."
            logger.info(msg)
            return dst_dir / src.name

        try:
            return src.move_into(dst_dir)

        # FIXME: This doesn’t actually get raised
        except FileExistsError:
            paths: Final = self._generate_unique_destination_path(
                dst_dir / src.name
            )

            for dst_path in islice(paths, self.config.max_collision_attempts):
                try:
                    return src.move(dst_path)

                except FileExistsError:
                    continue

            msg = f"{LogActions.FAILED}: Generating a unique path for "
            msg += f"'{src.name}' after {self.config.max_collision_attempts} "
            msg += "attempts."
            logger.error(msg)

        except PermissionError as e:
            msg = f"{LogActions.FAILED}: Permission denied for '{src.name}': "
            msg += f"{e}"
            logger.error(msg)

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

        for n in range(self.config.max_move_retries):
            delay = self.config.retry_delay_seconds * (2**n)

            msg = f"{LogActions.RETRYING} [{n + 1}/"
            msg += f"{self.config.max_move_retries}]: Moving '{src.name}' in "
            msg += f"{delay} seconds."
            logger.info(msg)
            sleep(delay)
            try:
                return src.move_into(dst_dir)

            except OSError:
                continue

        msg = f"{LogActions.FAILED}: Moving '{src.name}' after "
        msg += f"{self.config.max_move_retries} retries: {error}"
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
        padding: Final = len(str(self.config.max_collision_attempts))

        for n in count(1):
            yield path.with_stem(f"{stem}_{n:0{padding}}")
