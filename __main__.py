"""A CLI script to organize the contents of a directory"""

__author__ = "Charles Mesa Cayobit"


from argparse import ArgumentParser
from pathlib import Path

from organize_directory import main

if __name__ == "__main__":
    parser = ArgumentParser(prog="Organize Directory", description=__doc__)
    parser.add_argument("dir", type=Path, help="the directory to organize")
    args = parser.parse_args()

    main(args.dir)
