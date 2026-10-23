"""Tests for Merkle hashing and structural tree diffing in treediff.py."""

import hypothesis.strategies as st
import lxml.html
from hypothesis import given, settings

from warden_compare.treediff import (
    TreeAttrChange,
    TreeDelete,
    TreeInsert,
    TreeMove,
    TreeTextEdit,
    diff_trees,
    hash_tree,
)


def test_identical_doms_emit_no_ops_via_merkle_short_circuit():
    html = "<div><h1>Title</h1><p>Paragraph with text</p></div>"
    dom1 = lxml.html.fromstring(html)
    dom2 = lxml.html.fromstring(html)

    t1 = hash_tree(dom1)
    t2 = hash_tree(dom2)

    assert t1.node_hash == t2.node_hash
    ops = diff_trees(t1, t2)
    assert ops == []


def test_text_edit_detection():
    dom1 = lxml.html.fromstring("<div><p>Original Text</p></div>")
    dom2 = lxml.html.fromstring("<div><p>Modified Text</p></div>")

    t1 = hash_tree(dom1)
    t2 = hash_tree(dom2)
    ops = diff_trees(t1, t2)

    assert len(ops) == 1
    assert isinstance(ops[0], TreeTextEdit)
    assert ops[0].old_text == "Original Text"
    assert ops[0].new_text == "Modified Text"


def test_attribute_change_detection():
    dom1 = lxml.html.fromstring('<div class="old-class" id="box"><p>Text</p></div>')
    dom2 = lxml.html.fromstring('<div class="new-class" id="box"><p>Text</p></div>')

    t1 = hash_tree(dom1)
    t2 = hash_tree(dom2)
    ops = diff_trees(t1, t2)

    assert len(ops) == 1
    assert isinstance(ops[0], TreeAttrChange)
    assert ops[0].attr == "class"
    assert ops[0].old_value == "old-class"
    assert ops[0].new_value == "new-class"


def test_child_insertion_and_deletion():
    dom1 = lxml.html.fromstring("<ul><li>Item 1</li><li>Item 2</li></ul>")
    dom2 = lxml.html.fromstring("<ul><li>Item 1</li><li>Item 2</li><li>Item 3</li></ul>")

    t1 = hash_tree(dom1)
    t2 = hash_tree(dom2)
    ops = diff_trees(t1, t2)

    assert len(ops) == 1
    assert isinstance(ops[0], TreeInsert)
    assert ops[0].tag == "li"
    assert ops[0].text == "Item 3"

    # Reverse direction: deletion
    del_ops = diff_trees(t2, t1)
    assert len(del_ops) == 1
    assert isinstance(del_ops[0], TreeDelete)


def test_node_move_detection():
    dom1 = lxml.html.fromstring("<div><span id='a'>Alpha</span><span id='b'>Beta</span></div>")
    dom2 = lxml.html.fromstring("<div><span id='b'>Beta</span><span id='a'>Alpha</span></div>")

    t1 = hash_tree(dom1)
    t2 = hash_tree(dom2)
    ops = diff_trees(t1, t2)

    moves = [op for op in ops if isinstance(op, TreeMove)]
    assert len(moves) >= 1


def test_tail_text_change_is_hashed_and_reported():
    before = hash_tree(lxml.html.fromstring("<p><b>Price</b> $10</p>"))
    after = hash_tree(lxml.html.fromstring("<p><b>Price</b> $20</p>"))
    assert before.node_hash != after.node_hash
    assert any(
        isinstance(op, TreeTextEdit) and op.new_text == "$20" for op in diff_trees(before, after)
    )


def test_attribute_delimiters_do_not_alias_distinct_trees():
    before = hash_tree(lxml.html.fromstring('<p a="b;c=d">x</p>'))
    after = hash_tree(lxml.html.fromstring('<p a="b" c="d">x</p>'))
    assert before.node_hash != after.node_hash
    assert diff_trees(before, after)


def test_move_does_not_hide_adjacent_edit_or_duplicate_sibling():
    before = hash_tree(lxml.html.fromstring("<div><p>A</p><p>B</p><p>B</p></div>"))
    after = hash_tree(lxml.html.fromstring("<div><p>B</p><p>C</p><p>B</p></div>"))
    ops = diff_trees(before, after)
    assert any(
        isinstance(op, TreeTextEdit) and op.old_text == "A" and op.new_text == "C" for op in ops
    )


@settings(max_examples=30, deadline=None)
@given(
    tag=st.sampled_from(["div", "p", "span", "section"]),
    text=st.text(min_size=0, max_size=20),
)
def test_hypothesis_identical_structure_always_emits_empty_diff(tag: str, text: str):
    """Property test confirming structural identity guarantees empty tree diff."""
    safe_text = text.replace("<", "").replace(">", "").replace("&", "")
    html = f"<{tag}>{safe_text}</{tag}>"
    dom1 = lxml.html.fromstring(html)
    dom2 = lxml.html.fromstring(html)

    t1 = hash_tree(dom1)
    t2 = hash_tree(dom2)
    assert t1.node_hash == t2.node_hash
    assert diff_trees(t1, t2) == []
