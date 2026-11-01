"""Directed actual attachment closure, shared with the offline scene sweep."""


def attached_bodies(roots, active_pairs):
    result = set(roots)
    edges = tuple(active_pairs)
    pending = list(result)
    while pending:
        parent = pending.pop()
        for owner, child in edges:
            if owner == parent and child not in result:
                result.add(child)
                pending.append(child)
    return result
