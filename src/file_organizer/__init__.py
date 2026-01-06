"""Automated file organizer that supports sidecar files.

This module provides a rule-based file organizer that moves files into
specified directories based on their extensions or binary signatures. The
organizer supports configuration via INI and JSON formats for destination
mappings.

It also handles `.aae` and `.xmp` sidecar files, ensuring they follow their
parent files during organization.
"""

from .file_organizer import *
from .log_actions import *
from .organizer_config import *
