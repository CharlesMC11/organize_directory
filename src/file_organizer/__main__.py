"""A CLI script to organize the contents of a directory."""

import logging
from argparse import ArgumentParser
from pathlib import Path

from file_organizer import FileOrganizer
from file_organizer import __name__ as fo_name

logger = logging.getLogger(fo_name)
logger.setLevel(logging.DEBUG)


def main() -> None:
    logger.setLevel(logging.DEBUG)

    handler = logging.StreamHandler()
    logger.addHandler(handler)

    parser = ArgumentParser(prog="File Organizer", description=__doc__)
    parser.add_argument("dir", type=Path, help="the directory to organize")
    args = parser.parse_args()

    targets_file = Path(__file__).parents[2] / "extensions_map.ini"

    organizer = FileOrganizer.from_ini(targets_file)
    organizer.organize(args.dir)


if __name__ == "__main__":
    main()
