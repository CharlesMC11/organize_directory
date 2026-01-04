"""A CLI script to organize the contents of a directory."""

import logging
from argparse import ArgumentParser
from pathlib import Path

from file_organizer import FileOrganizer
from file_organizer import __name__ as fo_name

logger = logging.getLogger(fo_name)


def main() -> None:
    logger.setLevel(logging.INFO)

    handler = logging.StreamHandler()
    logger.addHandler(handler)

    # TODO: Add dry-run mode
    parser = ArgumentParser(prog="File Organizer", description=__doc__)
    parser.add_argument("dir", type=Path, help="the directory to organize")
    args = parser.parse_args()

    # FIXME: Don’t hardcode this here
    targets_file = Path(__file__).parents[2] / "extensions_map.ini"

    organizer = FileOrganizer.from_ini(targets_file)
    organizer.organize(args.dir)


if __name__ == "__main__":
    main()
