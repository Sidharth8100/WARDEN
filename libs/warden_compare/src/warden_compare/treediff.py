"""Merkle-tree hashing and structural DOM diffing."""

import hashlib
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
    node_hash = sha256(tag + sorted_attrs + text + concatenated_child_hashes).
    If two subtrees have identical hashes, they are guaranteed structurally and
    textually identical, allowing diff_trees to skip descending into them.
    """
    tag = element.tag if isinstance(element.tag, str) else "node"
    attrs = dict(sorted(element.attrib.items())) if hasattr(element, "attrib") else {}
    text = (element.text or "").strip() if hasattr(element, "text") else ""

    children: list[HashedNode] = []
    for child in element:
        if isinstance(child, lxml.html.HtmlElement):
            children.append(hash_tree(child))

    child_hashes = "".join(c.node_hash for c in children)
    attrs_str = ";".join(f"{k}={v}" for k, v in attrs.items())
    raw_payload = f"{tag}|{attrs_str}|{text}|{child_hashes}".encode()
    node_hash = hashlib.sha256(raw_payload).hexdigest()

    return HashedNode(
        tag=tag,
        attrs=attrs,
        text=text,
        children=children,
        node_hash=node_hash,
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

    # Child alignment and matching
    old_children = list(old.children)
    new_children = list(new.children)

    # Check for node moves: identical child hash present in both at different positions
    old_hash_to_idx = {c.node_hash: i for i, c in enumerate(old_children)}
    new_hash_to_idx = {c.node_hash: i for i, c in enumerate(new_children)}

    moved_hashes = set(old_hash_to_idx.keys()) & set(new_hash_to_idx.keys())
    # Exclude items that stayed at the exact same index
    actual_moves = {h for h in moved_hashes if old_hash_to_idx[h] != new_hash_to_idx[h]}

    for h in sorted(actual_moves):
        old_idx = old_hash_to_idx[h]
        new_idx = new_hash_to_idx[h]
        ops.append(
            TreeMove(
                from_path=f"{path}child[{old_idx}]",
                to_path=f"{path}child[{new_idx}]",
            )
        )

    # Positional child recursion for un-moved children
    min_len = min(len(old_children), len(new_children))
    for i in range(min_len):
        child_path = f"{path}child[{i}]"
        c_old = old_children[i]
        c_new = new_children[i]
        if c_old.node_hash not in actual_moves and c_new.node_hash not in actual_moves:
            ops.extend(diff_trees(c_old, c_new, path=child_path))

    # Deletions of excess old children
    if len(old_children) > len(new_children):
        for i in range(len(new_children), len(old_children)):
            ops.append(TreeDelete(path=f"{path}child[{i}]"))

    # Insertions of new children
    if len(new_children) > len(old_children):
        for i in range(len(old_children), len(new_children)):
            c = new_children[i]
            ops.append(TreeInsert(path=f"{path}child[{i}]", tag=c.tag, text=c.text))

    return ops
