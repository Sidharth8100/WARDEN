"""Visual diffing, SSIM structural similarity, and DisjointSet connected-components stub."""

import io
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity


@dataclass(frozen=True)
class BoundingBox:
    """Bounding box bounding a connected cluster of changed pixels."""

    min_row: int
    min_col: int
    max_row: int
    max_col: int

    @property
    def area(self) -> int:
        return (self.max_row - self.min_row + 1) * (self.max_col - self.min_col + 1)


@dataclass(frozen=True)
class VisualDiffResult:
    """Outcome of visual screenshot structural similarity comparison."""

    ssim_score: float
    bounding_boxes: list[BoundingBox]
    total_changed_pixels: int
    coverage_ratio: float


class DisjointSet:
    """A standard Union-Find / Disjoint Set Union
    structure over `n` elements (indices 0..n-1), supporting:
      - find(x): the representative of x's set, with PATH COMPRESSION.
      - union(x, y): merge the sets containing x and y, using UNION BY
        RANK (or by size — pick one, document which).
    Both operations must be (amortized) nearly O(1), not O(n).

    Hint L1: this is the exact same data structure used for
    Kruskal's MST algorithm and for connected-components problems —
    if you've done either on Codeforces, this is that structure.
    Hint L2: find() recurses (or loops) up a parent[] array until it
    hits a node that is its own parent (the root), THEN re-walks the
    path setting every visited node's parent directly to the root
    (path compression). union() compares rank[find(x)] and
    rank[find(y)] and attaches the shorter tree under the taller one.
    Hint L3: classic competitive-programming implementation; if
    you've implemented DSU before, this should take under 15 minutes.
    """

    def __init__(self, n: int) -> None:
        if n < 0:
            raise ValueError("negative set size")
        self.parent = list(range(n))
        self.size = [1] * n

    def find(self, x: int) -> int:
        if not 0 <= x < len(self.parent):
            raise IndexError(x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while x != root:
            previous = self.parent[x]
            self.parent[x] = root
            x = previous
        return root

    def union(self, x: int, y: int) -> None:
        left, right = self.find(x), self.find(y)
        if left == right:
            return
        if self.size[left] < self.size[right]:
            left, right = right, left
        self.parent[right] = left
        self.size[left] += self.size[right]


def cluster_changed_pixels(
    changed_mask: "np.ndarray[Any, np.dtype[np.bool_]]",
) -> list[BoundingBox]:
    """Given a 2D boolean array where True marks a
    pixel that changed between two screenshots, find CONNECTED
    COMPONENTS of changed pixels (4-connectivity: up/down/left/right,
    not diagonal — state this choice) and return one bounding box
    per component.

    Do not use scipy.ndimage.label, skimage.measure.label, or any
    other connected-components library — use YOUR DisjointSet above.
    The standard approach: assign each True pixel a unique index
    (row*width+col), union it with any True neighbor already seen,
    then do a final pass grouping pixel indices by root and computing
    each group's bounding box (min/max row, min/max col).

    Hint L1: a single pass over the array, unioning each True pixel
    with its up-neighbor and left-neighbor if they're also True, is
    sufficient — you don't need a multi-pass flood fill if you
    process pixels in row-major order, because by the time you reach
    pixel (r,c), its up and left neighbors have already been visited.
    Hint L2: after the union pass, do a second pass building a dict
    from find(pixel_index) -> list of (row, col) pairs, then reduce
    each list to a bounding box.
    Hint L3: numpy's array indexing and `np.argwhere(changed_mask)`
    may help you enumerate True pixel coordinates efficiently instead
    of a raw Python double-for-loop (which will be slow on large
    images — worth avoiding, though not strictly required for
    correctness on test-sized images).
    """
    if changed_mask.ndim != 2:
        raise ValueError("changed_mask must be a rectangular 2D array")
    height, width = changed_mask.shape
    groups = DisjointSet(height * width)
    for row, col in np.argwhere(changed_mask):
        row, col = int(row), int(col)
        cell = row * width + col
        if row and changed_mask[row - 1, col]:
            groups.union(cell, cell - width)
        if col and changed_mask[row, col - 1]:
            groups.union(cell, cell - 1)
    bounds: dict[int, BoundingBox] = {}
    for row, col in np.argwhere(changed_mask):
        row, col = int(row), int(col)
        root = groups.find(row * width + col)
        previous = bounds.get(root, BoundingBox(row, col, row, col))
        bounds[root] = BoundingBox(
            min(previous.min_row, row),
            min(previous.min_col, col),
            max(previous.max_row, row),
            max(previous.max_col, col),
        )
    return sorted(bounds.values(), key=lambda b: (b.min_row, b.min_col, b.max_row, b.max_col))


def diff_screenshots(
    before: bytes,
    after: bytes,
    *,
    diff_threshold: float = 0.9,
) -> VisualDiffResult:
    """Decode two PNG screenshots, compute full-image SSIM, and cluster changed regions.

    IMAGE SIZE ALIGNMENT:
    If before and after screenshots have different dimensions (e.g. dynamic content expanded page
    height), both images are cropped to the intersection of their minimum dimensions
    (min_width, min_height). This prevents distortion from stretching while accurately comparing
    the common rendered surface.
    """
    img_before = Image.open(io.BytesIO(before)).convert("L")
    img_after = Image.open(io.BytesIO(after)).convert("L")

    # Align image sizes to common minimum bounding rectangle
    w = min(img_before.width, img_after.width)
    h = min(img_before.height, img_after.height)
    if img_before.size != (w, h):
        img_before = img_before.crop((0, 0, w, h))
    if img_after.size != (w, h):
        img_after = img_after.crop((0, 0, w, h))

    arr_before = np.array(img_before)
    arr_after = np.array(img_after)

    # Compute Structural Similarity Index (SSIM) map
    ssim_val, ssim_map = structural_similarity(  # type: ignore[no-untyped-call]
        arr_before, arr_after, full=True
    )

    # Pixels where similarity is below threshold are tagged as changed
    changed_mask: np.ndarray[Any, np.dtype[np.bool_]] = ssim_map < diff_threshold

    total_changed = int(np.sum(changed_mask))
    total_pixels = w * h
    coverage = float(total_changed / total_pixels) if total_pixels > 0 else 0.0

    boxes = cluster_changed_pixels(changed_mask)

    return VisualDiffResult(
        ssim_score=float(ssim_val),
        bounding_boxes=boxes,
        total_changed_pixels=total_changed,
        coverage_ratio=coverage,
    )
