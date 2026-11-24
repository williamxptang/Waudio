#!/usr/bin/env python3
"""config.toml upgrades on load: a setting replaced by a newer one keeps your choice, once, with the
rest of the file (comments, other values) as it was.

  python3 backend/tests/test_config.py
"""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio import config

OLD_PILL = """# mine
[pill]
enabled = true                         # the floating pill
size = "large"
follow_pointer = {}                 # true: just under the mouse pointer
"""


def old_config(follow):
    path = Path(tempfile.mkdtemp()) / "config.toml"
    path.write_text(OLD_PILL.format(follow))
    return path


def follow_pointer_becomes_a_position():
    """On: under the pointer. Off: above the text. The old line goes; the new spot comes in."""
    for follow, where in (("true", "pointer"), ("false", "text")):
        path = old_config(follow)
        c = config.load(path)
        text = path.read_text()
        assert c["pill"]["position"] == where and "follow_pointer" not in c["pill"], (follow, c["pill"])
        assert "follow_pointer" not in text and text.count("position =") == 1, text
        assert c["pill"]["scale"] == 1.25 and "# mine" in text and "# the floating pill" in text
        assert len(c["pill"]["spot"]) == 2  # the newer key, from config.example.toml


def the_old_pill_sizes_become_its_scale():
    """Small, Medium and Large (before 0.4.125) become the size slider's 80 %, 100 % and 125 %; anything
    else written by hand, 100 %. The old line goes."""
    cases = config.PILL_SCALES | {"huge": 1.0}
    assert cases
    for name, scale in cases.items():
        path = Path(tempfile.mkdtemp()) / "config.toml"
        path.write_text(f'[pill]\nsize = "{name}"\n')
        c = config.load(path)
        assert c["pill"]["scale"] == scale and "size" not in c["pill"] and "size =" not in path.read_text(), (name, c["pill"])
    assert config.PILL_SCALES["small"] < config.PILL_SCALES["medium"] < config.PILL_SCALES["large"]


def only_true_means_on():
    """follow_pointer = "false" (a string, written by hand) is not on."""
    path = Path(tempfile.mkdtemp()) / "config.toml"
    path.write_text('[pill]\nfollow_pointer = "false"\n')
    assert config.load(path)["pill"]["position"] == "text"


def a_list_over_several_lines_is_not_a_section():
    """A line of a list that starts with "[" stays a value, and the old key after it is found."""
    path = Path(tempfile.mkdtemp()) / "config.toml"
    path.write_text('[pill]\nwords = [\n["a", "b"],\n]\nfollow_pointer = true\n')
    c = config.load(path)
    assert c["pill"]["position"] == "pointer" and c["pill"]["words"] == [["a", "b"]], c["pill"]


def a_chosen_position_is_never_changed():
    path = old_config("true")
    config.load(path)
    config.set_value("pill", "position", "fixed", path)
    before = path.read_text()
    assert config.load(path)["pill"]["position"] == "fixed" and path.read_text() == before


def a_section_from_a_newer_version_is_added():
    """A config.toml from before [ocr] (0.3.56) gets the section, as config.example.toml has it."""
    import re, tomllib
    example = config.EXAMPLE.read_text()
    old = re.sub(r"^\[ocr\]\n(?:(?!\[).*\n?)*", "", example, flags=re.M)
    assert "[ocr]" in example and "[ocr]" not in old
    path = Path(tempfile.mkdtemp()) / "config.toml"
    mine = re.sub(r'^voice = "[^"]*"', 'voice = "mine"', old, count=1, flags=re.M)  # whatever voice the example names
    assert mine != old, "no voice line in the example"
    path.write_text(mine)
    c = config.load(path)
    assert c["ocr"] == tomllib.loads(example)["ocr"] and c["read"]["voice"] == "mine", c.get("ocr")


follow_pointer_becomes_a_position()
only_true_means_on()
the_old_pill_sizes_become_its_scale()
a_list_over_several_lines_is_not_a_section()
a_chosen_position_is_never_changed()
a_section_from_a_newer_version_is_added()
print("config checks: ok")
