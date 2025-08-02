"""A mapping between a file extension and its destination

The directories are organized as such:

--Root/
  |--3D/
  |  |--Blender/
  |  |--Maya/
  |
  |--Audio/
  |
  |--Archives/
  |
  |--Documents/
  |
  |--Images/
  |  |--Raw/
  |
  |--Misc/
  |
  |--Programming/
  |  |--Assembly/
  |  |--C_Cpp/
  |  |--JavaScript/
  |  |--Python/
  |  |--Shell/
  |
  |--Videos/
"""

__author__ = "Charles Mesa Cayobit"


import os.path
from collections import defaultdict
from types import MappingProxyType

# DIRECTORY STRUCTURE ##########################################################


# 3D
_3D = "3D"
_3D_BLENDER = os.path.join(_3D, "Blender")
_3D_MAYA_DIR = os.path.join(_3D, "Maya")
# Audio
_AUDIO = "Audio"
# Archives
_ARCHIVES = "Archives"
# Documents
_DOCUMENTS = "Documents"
_DOCUMENTS_SPREADSHEETS = os.path.join(_DOCUMENTS, "Spreadsheets")
# Images
IMAGES = "Images"
IMAGES_RAW = os.path.join(IMAGES, "Raw")
# Misc
MISC = "Misc"
# Programming
_PROGRAMMING = "Programming"
_PROGRAMMING_ASSEMBLY = os.path.join(_PROGRAMMING, "Assembly")
_PROGRAMMING_C_CPP = os.path.join(_PROGRAMMING, "C:C++")
_PROGRAMMING_JAVASCRIPT = os.path.join(_PROGRAMMING, "JavaScript")
PROGRAMMING_PYTHON = os.path.join(_PROGRAMMING, "Python")
PROGRAMMING_SHELL = os.path.join(_PROGRAMMING, "Shell")
# Videos
_VIDEOS = "Videos"


# FILE EXTENSIONS MAPPING ######################################################


_TARGETS = {
    # 3D
    "abc": _3D,
    "fbx": _3D,
    "obj": _3D,
    # 3D/Blender
    "blend": _3D_BLENDER,
    "blend1": _3D_BLENDER,
    # 3D/Maya
    "ma": _3D_MAYA_DIR,
    "mb": _3D_MAYA_DIR,
    # Audio
    "mp3": _AUDIO,
    # Archives
    "7z": _ARCHIVES,
    "aar": _ARCHIVES,
    "zip": _ARCHIVES,
    "gz": _ARCHIVES,
    "xz": _ARCHIVES,
    "tar": _ARCHIVES,
    "txz": _ARCHIVES,
    # Documents
    "txt": _DOCUMENTS,
    "md": _DOCUMENTS,
    "rst": _DOCUMENTS,
    "doc": _DOCUMENTS,
    "docx": _DOCUMENTS,
    "pages": _DOCUMENTS,
    "indd": _DOCUMENTS,
    "pdf": _DOCUMENTS,
    # Documents/Spreadsheets
    "csv": _DOCUMENTS_SPREADSHEETS,
    "tsv": _DOCUMENTS_SPREADSHEETS,
    "xls": _DOCUMENTS_SPREADSHEETS,
    "xlsx": _DOCUMENTS_SPREADSHEETS,
    "numbers": _DOCUMENTS_SPREADSHEETS,
    # Images
    "exr": IMAGES,
    "gif": IMAGES,
    "heic": IMAGES,
    "jpeg": IMAGES,
    "jpg": IMAGES,
    "png": IMAGES,
    "webp": IMAGES,
    # Image Work Files
    "tif": IMAGES,
    "tiff": IMAGES,
    "clip": IMAGES,
    "cmc": IMAGES,
    "kra": IMAGES,
    "psb": IMAGES,
    "psd": IMAGES,
    # Images/Raw
    "dng": IMAGES_RAW,
    "orf": IMAGES_RAW,
    # Configuration Files
    "cfg": _PROGRAMMING,
    "env": _PROGRAMMING,
    # Programming/Assembly
    "s": _PROGRAMMING_ASSEMBLY,
    # Programming/C & C++
    "c": _PROGRAMMING_C_CPP,
    "cc": _PROGRAMMING_C_CPP,
    "cpp": _PROGRAMMING_C_CPP,
    "h": _PROGRAMMING_C_CPP,
    "hh": _PROGRAMMING_C_CPP,
    "hpp": _PROGRAMMING_C_CPP,
    "cmake": _PROGRAMMING_C_CPP,
    # Programming/JavaScript
    "gs": _PROGRAMMING_JAVASCRIPT,
    "js": _PROGRAMMING_JAVASCRIPT,
    # Programming/Python
    "py": PROGRAMMING_PYTHON,
    "pyc": PROGRAMMING_PYTHON,
    "pyi": PROGRAMMING_PYTHON,
    # Programming/Shell
    "bash": PROGRAMMING_SHELL,
    "sh": PROGRAMMING_SHELL,
    "zsh": PROGRAMMING_SHELL,
    "zwc": PROGRAMMING_SHELL,
    # Serialization
    "db": _PROGRAMMING,
    "json": _PROGRAMMING,
    "xml": _PROGRAMMING,
    "yml": _PROGRAMMING,
    # Videos
    "mkv": _VIDEOS,
    "mov": _VIDEOS,
    "mp4": _VIDEOS,
    "skba": _VIDEOS,
}


# EXPORTS ######################################################################


_tmp = set(_TARGETS.values())
_tmp.add(MISC)


DIRECTORIES = frozenset(_tmp)
TARGETS = MappingProxyType(defaultdict(lambda: MISC, _TARGETS))


__all__ = (
    "DIRECTORIES",
    "IMAGES",
    "IMAGES_RAW",
    "MISC",
    "PROGRAMMING_PYTHON",
    "PROGRAMMING_SHELL",
    "TARGETS",
)
