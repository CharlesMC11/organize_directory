"""A CLI script to organize the contents of a directory."""

import logging
from argparse import ArgumentParser
from pathlib import Path

from file_organizer import FileOrganizer, OrganizerConfig, __name__

logger = logging.getLogger(__name__)


def main() -> None:
    logger.setLevel(logging.INFO)

    handler = logging.StreamHandler()
    logger.addHandler(handler)

    parser = ArgumentParser(prog="File Organizer", description=__doc__)
    parser.add_argument("dir", type=Path, help="the directory to organize")
    parser.add_argument("config", type=Path, help="the config file to use")
    parser.add_argument(
        "--dry-run", action="store_true", help="show what would be done"
    )
    args = parser.parse_args()

    config = OrganizerConfig.from_ini(args.config, dry_run=args.dry_run)
    organizer = FileOrganizer(config)
    organizer.organize(args.dir)


if __name__ == "__main__":
    main()
