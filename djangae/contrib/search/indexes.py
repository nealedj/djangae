from search.indexes import Index


def get_index_for_doc(document_cls):
    """Return a search index based on a Document class"""
    parts = document_cls.__module__.split('.')
    return Index('_'.join([parts[0], parts[2]]))
