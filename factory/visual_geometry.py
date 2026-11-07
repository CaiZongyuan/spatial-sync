"""Canonical surface payload witnesses shared across Blender and file decoders."""

import hashlib
import struct

import numpy as np


def canonical_loop(corners):
    """Choose one cyclic start/direction without changing polygon edges."""
    sequence = [tuple(row) for row in corners.tolist()]

    def least_rotation(values):
        count, first, second, offset = len(values), 0, 1, 0
        doubled = values * 2
        while first < count and second < count and offset < count:
            left, right = doubled[first + offset], doubled[second + offset]
            if left == right:
                offset += 1
            elif left > right:
                first += offset + 1
                if first == second:
                    first += 1
                offset = 0
            else:
                second += offset + 1
                if first == second:
                    second += 1
                offset = 0
        start = min(first, second)
        return values[start:] + values[:start]

    return np.asarray(min(least_rotation(sequence), least_rotation(sequence[::-1])), dtype="<f4")


def surface_signature(points, counts, indices):
    """Ignore winding/zero-area faces, retaining every nondegenerate polygon."""
    points = np.asarray(points, dtype="<f4").copy()
    counts, indices = np.asarray(counts, dtype=np.int64), np.asarray(indices, dtype=np.int64)
    if (points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all()
            or counts.ndim != 1 or indices.ndim != 1 or np.any(counts < 3)
            or int(np.sum(counts)) != len(indices) or np.any(indices < 0)
            or np.any(indices >= len(points))):
        raise ValueError("Invalid surface points or face indices")
    points[points == 0] = 0  # Canonicalize the sign of floating point zero.
    starts, surface, face_counts = np.cumsum(counts) - counts, [], {}
    digest = hashlib.sha256()
    for count in np.unique(counts):
        offsets = starts[counts == count, None] + np.arange(count)[None, :]
        corners = points[indices[offsets]]
        vectors = corners.astype(np.float64)
        vectors -= vectors[:, :1]
        area_vectors = np.sum(np.cross(vectors, np.roll(vectors, -1, axis=1)), axis=1)
        corners = corners[np.any(area_vectors != 0, axis=1)]
        if not len(corners):
            continue
        face_counts[str(count)] = len(corners)
        surface.append(corners.reshape(-1, 3))
        if count == 3:
            # For triangles, all six permutations are rotations/reversal.
            order = np.lexsort((corners[:, :, 2], corners[:, :, 1], corners[:, :, 0]), axis=1)
            ordered = np.take_along_axis(corners, order[:, :, None], axis=1)
        else:
            ordered = np.stack([canonical_loop(face) for face in corners])
        rows = np.ascontiguousarray(ordered).reshape(len(corners), -1)
        canonical = np.sort(rows.view(np.dtype((np.void, rows.shape[1] * 4))).reshape(-1))
        digest.update(struct.pack("<II", int(count), len(corners)))
        digest.update(canonical.tobytes())
    if not surface:
        raise ValueError("Surface has no nondegenerate faces")
    vertices = np.unique(np.concatenate(surface), axis=0)
    return {"facesHash": digest.hexdigest(), "faceCounts": face_counts,
            "vertexHash": hashlib.sha256(vertices.tobytes()).hexdigest(),
            "uniqueVertices": len(vertices)}, vertices
