import pytest

from file_organizer.organizer_config import (
    CONFIG_ENCODING,
    MissingRequiredFieldsError,
    OrganizerConfig,
)

DIR_NAMES = r"""
[dir_names]
archives = Archives
images = Images
images_raw = Images/Raw
programming = Programming
python = Programming/Python
shell = Programming/Shell
"""

EXT_TO_RE = r"""
[ext_to_re]
png = \x89PNG
py = #!/.*?python
sh = #!/.*?sh
zip = PK\x03\x04
"""

EXT_TO_DIR = r"""
[ext_to_dir]
jpeg = images
png = images
dng = images_raw
cfg = programming
py = python
sh = shell
zip = archives
"""


def test__read_validated_config(tmp_path):
    conf = tmp_path / "config.ini"

    with pytest.raises(FileNotFoundError):
        OrganizerConfig.from_ini(conf)

    conf.mkdir(parents=True, exist_ok=True)
    with pytest.raises(FileNotFoundError):
        OrganizerConfig.from_ini(conf)


def test__validate_config_fields(tmp_path):
    conf = tmp_path / "config.ini"
    conf.write_text(EXT_TO_RE, encoding=CONFIG_ENCODING)
    with pytest.raises(
        MissingRequiredFieldsError, match="(dir_names|ext_to_dir)"
    ):
        OrganizerConfig.from_ini(conf)


def test__sanitize_ext():
    assert "" == OrganizerConfig._sanitize_ext(" .  ")
    assert ".jpeg" == OrganizerConfig._sanitize_ext(" .JPEG ")
    assert ".tar.gz" == OrganizerConfig._sanitize_ext(" ...TAR.GZ ")


def test__compile_signature_re():
    validated_ext = ".png", ".zip"
    ext_to_re = {" .PNG ": r"\x89PNG", " ...ZIP  ": r"ZIP\x03\x04"}

    assert OrganizerConfig._compile_signature_re([], ext_to_re) is None

    assert (
        OrganizerConfig._compile_signature_re(validated_ext, {" . ": ".+?"})
        is None
    )

    pair = OrganizerConfig._compile_signature_re(validated_ext, ext_to_re)
    assert pair is not None

    pattern, mapping = pair
    assert (
        pattern.pattern
        == b"(?P<g__png>(?>\x89PNG))|(?P<g__zip>(?>ZIP\x03\x04))"
    )
    assert mapping == {"g__png": ".png", "g__zip": ".zip"}


def test___init__(tmp_path):
    dir_names = "Photos", "Photos/Raw", "Photos", "Photos/Raw"
    ext_to_dir = {"   ...PNG   ": "Photos"}
    ext_to_re = {"   ...PNG   ": r"\x89PNG"}

    o1 = OrganizerConfig(dir_names, ext_to_dir, ext_to_re)
    assert o1.dir_names == {
        "Photos",
        "Photos/Raw",
        OrganizerConfig.DEFAULT_DIR_NAME,
    }
    assert o1.ext_to_dir == {".png": "Photos"}
    assert o1.signatures_re is not None
    assert o1.signatures_re.pattern == b"(?P<g__png>(?>\x89PNG))"

    o2 = OrganizerConfig(
        dir_names,
        ext_to_dir,
        ext_to_re,
        max_move_retries=99,
        retry_delay_seconds=1.0,
        max_collision_attempts=1,
        dry_run=True,
    )
    assert o2.max_move_retries == 99
    assert o2.retry_delay_seconds == pytest.approx(1)
    assert o2.max_collision_attempts == 1
    assert o2.dry_run is True


def test_from_ini(tmp_path):
    conf = tmp_path / "conf.cfg"
    conf.write_text(EXT_TO_RE, encoding=CONFIG_ENCODING)

    with pytest.raises(
        MissingRequiredFieldsError, match="(dir_names|ext_to_dir)"
    ):
        OrganizerConfig.from_ini(conf)

    conf.write_text(
        DIR_NAMES + EXT_TO_DIR + EXT_TO_RE, encoding=CONFIG_ENCODING
    )
    OrganizerConfig.from_ini(conf)


def test_from_json(tmp_path):
    import json

    conf = tmp_path / "conf.json"
    j: dict[str, dict] = {
        "dir_names": {
            "archives": "Archives",
            "images": "Images",
            "images_raw": "Images/Raw",
            "programming": "Programming",
            "python": "Python",
            "shell": "Shell",
        }
    }
    with conf.open("x", encoding=CONFIG_ENCODING) as f:
        json.dump(j, f)

    with pytest.raises(MissingRequiredFieldsError, match="ext_to_dir"):
        OrganizerConfig.from_json(conf)

    j.update(
        {
            "ext_to_dir": {
                "archives": [".zip"],
                "images": [".jpeg", ".jpg", ".png"],
                "images_raw": [".dng"],
                "programming": [".cfg"],
                "python": [".py"],
                "shell": [".sh"],
            }
        }
    )
    with conf.open("w", encoding=CONFIG_ENCODING) as f:
        json.dump(j, f)

    c = OrganizerConfig.from_json(conf)
    assert c.dir_names == {
        "Archives",
        "Images",
        "Images/Raw",
        "Programming",
        "Python",
        "Shell",
        OrganizerConfig.DEFAULT_DIR_NAME,
    }
    assert c.ext_to_dir == {
        ".zip": "Archives",
        ".jpeg": "Images",
        ".jpg": "Images",
        ".png": "Images",
        ".dng": "Images/Raw",
        ".cfg": "Programming",
        ".py": "Python",
        ".sh": "Shell",
    }
    assert c.signatures_re is None

    j.update(
        {
            "ext_to_re": {
                "   .PNG   ": r"\x89PNG",
            }
        }
    )
    with conf.open("w", encoding=CONFIG_ENCODING) as f:
        json.dump(j, f)

    o2 = OrganizerConfig.from_json(conf)
    assert o2.signatures_re is not None
    assert o2.signatures_re.pattern == b"(?P<g__png>(?>\x89PNG))"
