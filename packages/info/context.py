"""Information identity is owned by the Catalog, including targets without geometry."""


def information_revision(target):
    return target.catalog.sceneRevision if target.catalog is not None else None
