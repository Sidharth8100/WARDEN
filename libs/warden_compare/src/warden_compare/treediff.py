"""Merkle-tree hashing and structural DOM diffing."""

import hashlib
import json
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

import lxml.html  # type: ignore[import-untyped]


@dataclass(frozen=True)
class HashedNode:
    """DOM node annotated with a recursive Merkle content hash."""

    tag: str
    attrs: dict[str, str]
    text: str
    children: list["HashedNode"]
    node_hash: str
    tail: str = ""


@dataclass(frozen=True)
class TreeInsert:
    path: str
    tag: str
    text: str


@dataclass(frozen=True)
class TreeDelete:
    path: str


@dataclass(frozen=True)
class TreeMove:
    from_path: str
    to_path: str


@dataclass(frozen=True)
class TreeTextEdit:
    path: str
    old_text: str
    new_text: str


@dataclass(frozen=True)
class TreeAttrChange:
    path: str
    attr: str
    old_value: str | None
    new_value: str | None


TreeOp = TreeInsert | TreeDelete | TreeMove | TreeTextEdit | TreeAttrChange


def hash_tree(element: Any) -> HashedNode:
    """Recursively compute Merkle tree hashes for an lxml element tree.

    MERKLE INVARIANT:
    Hash an unambiguous JSON tuple of normalized fields and child hashes.
    Tail text belongs to an element's following text segment and must be included.
    Equal hashes allow a subtree skip under SHA-256's collision assumption.
    """
    tag = element.tag if isinstance(element.tag, str) else "node"
    attrs = dict(sorted(element.attrib.items())) if hasattr(element, "attrib") else {}
    text = (element.text or "").strip() if hasattr(element, "text") else ""
    tail = (element.tail or "").strip() if hasattr(element, "tail") else ""

    children: list[HashedNode] = []
    for child in element:
        if isinstance(child, lxml.html.HtmlElement):
            children.append(hash_tree(child))

    raw_payload = json.dumps(
        [tag, attrs, text, tail, [c.node_hash for c in children]],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    node_hash = hashlib.sha256(raw_payload).hexdigest()

    return HashedNode(
        tag=tag,
        attrs=attrs,
        text=text,
        children=children,
        node_hash=node_hash,
        tail=tail,
    )


def diff_trees(old: HashedNode, new: HashedNode, path: str = "/") -> list[TreeOp]:
    """Perform top-down structural tree diffing with Merkle hash short-circuiting."""
    # MERKLE SHORT-CIRCUIT: Provably identical subtrees emit zero ops immediately
    if old.node_hash == new.node_hash:
        return []

    ops: list[TreeOp] = []

    # If tag changes, replace subtree
    if old.tag != new.tag:
        ops.append(TreeDelete(path=path))
        ops.append(TreeInsert(path=path, tag=new.tag, text=new.text))
        return ops

    # Attribute modifications
    all_attr_keys = sorted(set(old.attrs.keys()) | set(new.attrs.keys()))
    for key in all_attr_keys:
        old_val = old.attrs.get(key)
        new_val = new.attrs.get(key)
        if old_val != new_val:
            ops.append(TreeAttrChange(path=path, attr=key, old_value=old_val, new_value=new_val))

    # Text modification
    if old.text != new.text:
        ops.append(TreeTextEdit(path=path, old_text=old.text, new_text=new.text))
    if old.tail != new.tail:
        ops.append(
            TreeTextEdit(path=f"{path.rstrip('/')}/tail()", old_text=old.tail, new_text=new.tail)
        )

    # Child alignment and matching
    old_children = list(old.children)
    new_children = list(new.children)

    # Match every duplicate occurrence independently. A hash->one-index map
    # silently drops siblings; skipping both positions of a move can also hide
    # the replacement next to it (old A,B -> new B,C).
    positions: dict[str, deque[int]] = defaultdict(deque)
    for index, child in enumerate(new_children):
        positions[child.node_hash].append(index)
    matched_old, matched_new = set(), set()
    prefix = path.rstrip("/")
    for old_index, child in enumerate(old_children):
        if positions[child.node_hash]:
            new_index = positions[child.node_hash].popleft()
            matched_old.add(old_index)
            matched_new.add(new_index)
            if old_index != new_index:
                ops.append(TreeMove(f"{prefix}/child[{old_index}]", f"{prefix}/child[{new_index}]"))
    left = [(i, c) for i, c in enumerate(old_children) if i not in matched_old]
    right = [(i, c) for i, c in enumerate(new_children) if i not in matched_new]
    paired = min(len(left), len(right))
    for (_, old_child), (index, new_child) in zip(left[:paired], right[:paired], strict=True):
        ops.extend(diff_trees(old_child, new_child, path=f"{prefix}/child[{index}]"))
    for index, _ in left[paired:]:
        ops.append(TreeDelete(path=f"{prefix}/child[{index}]"))
    for index, child in right[paired:]:
        ops.append(TreeInsert(path=f"{prefix}/child[{index}]", tag=child.tag, text=child.text))

    return ops
