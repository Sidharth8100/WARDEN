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
