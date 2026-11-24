#!/usr/bin/env python3
"""Read aloud in the browser (browser/tools/reader-test.mjs --only=page, against a stand-in for the app, in a
throwaway headless Chrome): what is read on Wikipedia, ordinary and news sites (Reuters' <div> paragraphs, the
Guardian's timeline, no link lists, captions or sign-up lines); ▶ beside a paragraph, ⏸, ■ and the bookmark;
"Read this page"; a page over the size limit; choices the app refuses; nothing on a site the manifest keeps it off; the popup.

  python3 backend/tests/test_reader_page.py
"""
# needs: node, chrome
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_reader_follow import reads_aloud

reads_aloud("page")
print("reader page checks: ok")
