"""Student test suite for Stub #3: DisjointSet and visual diff clustering.

STUDENT STUB TEST:
Tagged with @pytest.mark.student_todo. Excluded from default `make check`.
Run explicitly via `make check-todos`.
"""

from collections import deque

import hypothesis.strategies as st
import numpy as np
import pytest
from hypothesis import given, settings

from warden_compare.visualdiff import (
    BoundingBox,
    DisjointSet,
    cluster_changed_pixels,
)


def _bfs_reference_clusters(mask: np.ndarray) -> list[set[tuple[int, int]]]:
    """Independent BFS flood-fill oracle computing 4-connected components.

    ALGORITHMIC DIVERSITY NOTE:
    This oracle uses queue-based Breadth-First Search (flood fill), which is a completely
    different algorithmic approach from Disjoint Set Union (DSU / Union-Find).
    """
    rows, cols = mask.shape
    visited: set[tuple[int, int]] = set()
    clusters: list[set[tuple[int, int]]] = []

    for r in range(rows):
        for c in range(cols):
            if bool(mask[r, c]) and (r, c) not in visited:
                cluster: set[tuple[int, int]] = set()
                queue: deque[tuple[int, int]] = deque([(r, c)])
                visited.add((r, c))
                while queue:
                    curr_r, curr_c = queue.popleft()
                    cluster.add((curr_r, curr_c))
                    # 4-connectivity: up, down, left, right ONLY (no diagonal movement)
                    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                        nr, nc = curr_r + dr, curr_c + dc
                        if (
                            0 <= nr < rows
                            and 0 <= nc < cols
                            and bool(mask[nr, nc])
                            and (nr, nc) not in visited
                        ):
                            visited.add((nr, nc))
                            queue.append((nr, nc))
                clusters.append(cluster)
    return clusters


def _oracle_bounding_boxes(clusters: list[set[tuple[int, int]]]) -> list[BoundingBox]:
    """Convert pixel coordinate sets into BoundingBoxes sorted in canonical order."""
    boxes = []
    for cluster in clusters:
        rows = [r for r, _ in cluster]
        cols = [c for _, c in cluster]
        boxes.append(
            BoundingBox(
                min_row=min(rows),
                min_col=min(cols),
                max_row=max(rows),
                max_col=max(cols),
            )
        )
    return sorted(boxes, key=lambda b: (b.min_row, b.min_col, b.max_row, b.max_col))


@pytest.mark.student_todo
class TestDisjointSetStudent:
    """Tests for DisjointSet class implementation."""

    def test_basic_union_and_find(self):
        dsu = DisjointSet(5)
        # Initially each element is its own representative
        assert dsu.find(0) != dsu.find(1)

        dsu.union(0, 1)
        assert dsu.find(0) == dsu.find(1)

        dsu.union(1, 2)
        assert dsu.find(0) == dsu.find(2)

        # Disjoint set remains separate
        assert dsu.find(3) != dsu.find(0)


@pytest.mark.student_todo
class TestClusterChangedPixelsStudent:
    """Tests for 4-connectivity pixel clustering and bounding box generation."""

    def test_4_connectivity_vs_8_connectivity_diagonal_check(self):
        """DIAGONAL ADJACENCY MUST NOT MERGE IN 4-CONNECTIVITY.

        Grid layout (3x3):
        [True,  False, False]
        [False, True,  False]  -> (0,0) and (1,1) touch diagonally only.
        [False, False, True ]  -> (1,1) and (2,2) touch diagonally only.

        Under 4-connectivity, this grid has THREE separate clusters.
        Under 8-connectivity, it would erroneously report ONE cluster.
        """
        grid = np.array(
            [
                [True, False, False],
                [False, True, False],
                [False, False, True],
            ],
            dtype=bool,
        )
        boxes = cluster_changed_pixels(grid)
        assert len(boxes) == 3, (
            f"Expected 3 separate clusters under 4-connectivity, got {len(boxes)}"
        )

    def test_hand_built_distinct_clusters(self):
        """Verify multiple separate rectangular pixel blocks produce distinct boxes."""
        grid = np.zeros((10, 10), dtype=bool)
        # Cluster 1: top-left 2x2
        grid[1:3, 1:3] = True
        # Cluster 2: bottom-right 3x3
        grid[6:9, 6:9] = True

        boxes = cluster_changed_pixels(grid)
        sorted_boxes = sorted(boxes, key=lambda b: (b.min_row, b.min_col))
        assert len(sorted_boxes) == 2
        assert sorted_boxes[0] == BoundingBox(min_row=1, min_col=1, max_row=2, max_col=2)
        assert sorted_boxes[1] == BoundingBox(min_row=6, min_col=6, max_row=8, max_col=8)

    @settings(max_examples=25, deadline=None)
    @given(
        grid_data=st.tuples(st.integers(5, 10), st.integers(5, 10)).flatmap(
            lambda dimensions: st.lists(
                st.lists(st.booleans(), min_size=dimensions[1], max_size=dimensions[1]),
                min_size=dimensions[0],
                max_size=dimensions[0],
            )
        )
    )
    def test_hypothesis_agreement_with_bfs_reference_oracle(self, grid_data: list[list[bool]]):
        """Property test: DSU clustering must produce identical bounding boxes to BFS oracle."""
        mask = np.array(grid_data, dtype=bool)

        student_boxes = cluster_changed_pixels(mask)
        sorted_student = sorted(
            student_boxes, key=lambda b: (b.min_row, b.min_col, b.max_row, b.max_col)
        )

        oracle_clusters = _bfs_reference_clusters(mask)
        sorted_oracle = _oracle_bounding_boxes(oracle_clusters)

        assert sorted_student == sorted_oracle
