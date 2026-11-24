#!/usr/bin/env python3
"""A read page -> text to speak (engine/pagetext.py): one case per kind of markup the OCR readers write
(Markdown, HTML and Markdown tables, LaTeX formulas, inline maths, DocTags-style tags), wrapped lines
joined, headers / footers / page numbers left out, every block a sentence.

  python3 backend/tests/test_pagetext.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio.engine.pagetext import formula, prose, speakable, table


def b(text, label="text", kind="text"):
    return {"label": label, "kind": kind, "text": text}


def markdown_is_read_without_its_marks():
    got = prose("## The **mean** and `variance`\n\n- first *point*\n- second point\n1. third\n> quoted\n---\n"
                "See [the table](http://x.org/t) ![a chart](c.png) below.")
    assert got == "The mean and variance.\nfirst point.\nsecond point.\nthird.\nquoted.\nSee the table below.", got


def wrapped_lines_join_up():
    assert prose("The quick brown fox jumps over the lazy\ndog, then rests.") == "The quick brown fox jumps over the lazy dog, then rests."
    assert prose("an exam-\nple of wrapping") == "an example of wrapping."
    assert prose("well-\nKnown") == "well- Known."  # a capital after the hyphen: a real hyphen, kept
    assert prose("这是一个很长的\n句子，换了行。") == "这是一个很长的句子，换了行。"
    assert prose("First paragraph.\n\nSecond paragraph") == "First paragraph.\nSecond paragraph."


def an_html_table_is_read_row_by_row():
    html = ('<table border="1"><tr><td></td><td>clever</td><td>quiet</td></tr>'
            "<tr><td>Lily</td><td></td><td>√</td></tr><tr><td></td><td></td><td></td></tr>"
            "<tr><th>Tom &amp; Ann</th><td><b>yes</b></td><td>no</td></tr></table>")
    assert table(html) == "clever, quiet.\nLily, √.\nTom & Ann, yes, no.", table(html)


def a_markdown_table_is_read_row_by_row():
    md = "| Name | Score |\n|---|:---:|\n| Ann | 9 |\n|  |  |\n| Bob | 7 |"
    assert table(md) == "Name, Score.\nAnn, 9.\nBob, 7.", table(md)
    assert table("just words, no table") == "just words, no table."


def a_markdown_table_inside_text_is_read_row_by_row():
    got = prose("Scores below.\n| Name | Score |\n|---|---|\n| Ann | 9 |\nThat is all.")
    assert got == "Scores below.\nName, Score.\nAnn, 9.\nThat is all.", got
    assert prose("a | b is not a table") == "a | b is not a table."  # one line with a pipe: just text


def a_doctags_table_is_read_row_by_row():
    otsl = "<otsl><loc_10><loc_20><loc_300><loc_80><ched>Name<ched>Score<nl><fcel>Ann<fcel>9<nl><ecel><ecel><nl><fcel>Bob<fcel>7<nl></otsl>"
    assert table(otsl) == "Name, Score.\nAnn, 9.\nBob, 7.", table(otsl)
    assert prose("<text><loc_1><loc_2><loc_3><loc_4>Before.</text>" + otsl + "<text>After.</text>") == \
        "Before.\nName, Score.\nAnn, 9.\nBob, 7.\nAfter.", prose("<text>Before.</text>" + otsl)


def a_formula_is_read_in_words():
    for tex, said in (
        ("$$\nv = (1 / 2) (1 0 / 1 0 0) ^ {- 2} = 5 0\n$$", "v equals (1 / 2) (10 / 100) to the power minus 2 equals 50"),
        ("$$\\nu _ {3} = \\frac {0 . 1}{3 4 . 8 ^ {*} \\sqrt {3}} = 0. 1 6 6 \\%\n$$",
         "nu sub 3 equals 0.1 over 34.8 to the power times the square root of 3 equals 0.166 percent"),
        ("\\[ x^2 + y^{3} \\leq \\alpha_i \\]", "x squared plus y cubed less than or equal to alpha sub i"),
        ("\\frac{\\frac{a}{b}}{c} \\neq \\mathrm{d}", "a over b over c not equal to d"),
    ):
        assert formula(tex) == said, (tex, formula(tex))


def inline_maths_and_tags_inside_text():
    assert prose("The mean $E[X] = \\frac{1}{n}$ holds.") == "The mean E[X] equals 1 over n holds."
    assert prose("It costs $5 today and $6 tomorrow.") == "It costs $5 today and $6 tomorrow."  # prices, not maths
    assert prose("<doctag><text>Hello<br/>world</text></doctag>") == "Hello world."  # a <br/> only wraps


def a_page_reads_in_order_without_its_furniture():
    page = [b("Journal of Things", "header"), b("Who Am I?", "paragraph_title"), b("I am a fox", "text"),
            b("<table><tr><td>a</td><td>b</td></tr></table>", "table", "table"),
            b("$$x^2$$", "display_formula", "formula"), b("(3)", "formula_number"), b("12", "number"), b("© 2008", "footer"),
            b("   ", "text"), b("最后一段", "text")]
    assert speakable(page) == "Who Am I?\nI am a fox.\na, b.\nx squared.\n最后一段。", speakable(page)
    assert speakable([]) == ""


markdown_is_read_without_its_marks()
wrapped_lines_join_up()
an_html_table_is_read_row_by_row()
a_markdown_table_is_read_row_by_row()
a_markdown_table_inside_text_is_read_row_by_row()
a_doctags_table_is_read_row_by_row()
a_formula_is_read_in_words()
inline_maths_and_tags_inside_text()
a_page_reads_in_order_without_its_furniture()
print("page text checks: ok")
