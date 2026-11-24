#!/usr/bin/env python3
"""The project's shell scripts never delete: what they replace or remove goes to the Trash (`trash`),
so a wrong path can be put back. A delete command (rm, rmdir, unlink as a word anywhere, find -delete,
git clean) in a tracked .sh file fails; the same word in a comment or in another word (perform,
unlinked, rm.txt) does not.

  python3 backend/tests/test_scripts.py
"""
import re, subprocess
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]

# the command as a word anywhere (sudo rm, xargs rm, -exec rm, /bin/rm, \rm …), find -delete, git clean
DELETE = re.compile(r"(?<![\w.-])(rm|rmdir|unlink)(?![\w.-])|\s-delete\b|\bgit\s+clean\b")


def deletes(text):
    """(line number, line) of every delete command, comments left out."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        code = re.sub(r"(^|\s)#.*$", "", line)
        if DELETE.search(code):
            out.append((n, line.strip()))
    return out


def the_scan_finds_a_delete():
    cases = ['rm -rf "$OUT"', 'if [ -e x ]; then rm -f "$F"; fi', "  rmdir old", "x && unlink y",
             "find . -name '*.tmp' -delete", "for f in a b; do rm $f; done", "sudo rm x", "FOO=1 rm x",
             "/bin/rm x", "\\rm x", "ls | xargs rm", "find . -exec rm {} +", "git clean -fd", 'x=$(rm a)']
    assert cases
    for line in cases:
        assert deletes(line), line
    assert not deletes('trash "$OUT"  # never rm -rf') and not deletes("perform the build") \
        and not deletes("echo removed; unlinked=1") and not deletes("cp rm.txt firm-rm.log")


def no_script_deletes():
    scripts = [ROOT / p for p in subprocess.run(["git", "ls-files", "*.sh"], cwd=ROOT, capture_output=True,
                                                 text=True, check=True).stdout.split()]
    assert scripts, "no tracked .sh file found"
    found = [f"{p.relative_to(ROOT)}:{n}: {l}" for p in scripts for n, l in deletes(p.read_text())]
    assert not found, "deletes instead of trash:\n  " + "\n  ".join(found)


the_scan_finds_a_delete()
no_script_deletes()
print("script checks: ok")
