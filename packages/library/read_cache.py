"""Bounded derived metadata cache; actual use still verifies immutable content."""

from collections import OrderedDict
from threading import RLock


def stamps(paths):
    result = []
    for path in paths:
        try:
            value = path.lstat()
            result.append((value.st_mode, value.st_ino, value.st_size,
                           value.st_mtime_ns, value.st_ctime_ns))
        except OSError:
            result.append(None)
    return tuple(result)


def tree_paths(root, boundary=None):
    """Directory stamps invalidate additions, removals and changed link ancestors."""
    boundary = boundary or root.parent
    if not root.is_relative_to(boundary):
        raise ValueError("Cache input is outside its configured root")
    paths = {root}
    parent = root
    while parent != boundary:
        parent = parent.parent
        paths.add(parent)
    if root.is_dir():
        paths.update(root.rglob("*"))
    return paths


class ReadCache:
    def __init__(self, limit=128):
        self.limit = limit
        self.entries = OrderedDict()
        self.lock = RLock()

    def get(self, key):
        with self.lock:
            entry = self.entries.get(key)
            if entry is None:
                return None
            paths, recorded, value = entry
            if stamps(paths) != recorded:
                self.entries.pop(key)
                return None
            self.entries.move_to_end(key)
            return value

    def put(self, key, value, paths, recorded):
        paths = tuple(sorted(set(paths)))
        with self.lock:
            if stamps(paths) != recorded:
                return value
            self.entries[key] = (paths, recorded, value)
            self.entries.move_to_end(key)
            while len(self.entries) > self.limit:
                self.entries.popitem(last=False)
        return value

    def clear(self):
        with self.lock:
            self.entries.clear()
