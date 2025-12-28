"""A CLI script to organize the contents of a directory."""

__author__ = "Charles Mesa Cayobit"

from argparse import ArgumentParser
from pathlib import Path

from file_organizer import FileOrganizer

if __name__ == "__main__":
    parser = ArgumentParser(prog="Organize Directory", description=__doc__)
    parser.add_argument("dir", type=Path, help="the directory to organize")
    args = parser.parse_args()

    targets_file = Path(__file__).parents[2] / "extensions_map.cfg"

    organizer = FileOrganizer.from_ini(targets_file)
    organizer.organize(args.dir)
