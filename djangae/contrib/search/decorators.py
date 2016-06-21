from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from django.utils.module_loading import import_string

from search.indexes import Index

from .registry import registry
from .utils import (
    get_default_index_name,
    get_rank,
    get_uid,
    indexing_is_enabled,
)


def connect_signals(model_class, document_class, index_name, rank=None):
    """Wire up `model_class`'s `post_save` and `pre_delete` signals to
    receivers that will index and unindex instances when saved and deleted.

    Args:
        * model_class: The model class to connect signals for
        * document_class: The document class to index instances of
            `model_class` with
        * index_name: The name of the index to put/delete to/from
        * rank: See `searchable`
    """
    uid = get_uid(model_class, document_class, index_name)

    @receiver(post_save, sender=model_class, dispatch_uid=uid, weak=False)
    def index(sender, instance, **kwargs):
        if indexing_is_enabled():
            doc = document_class(
                doc_id=str(instance.pk),
                _rank=get_rank(instance, rank=rank)
            )
            doc.build_base(instance)
            index = Index(index_name)
            index.put(doc)

    @receiver(pre_delete, sender=model_class, dispatch_uid=uid, weak=False)
    def unindex(sender, instance, **kwargs):
        if indexing_is_enabled():
            index = Index(index_name)
            index.delete(str(instance.pk))


def add_search_query_method(model_class):
    """Add a method to the model class that will return a search query for
    that model.
    """
    def search_query(cls, ids_only=None, document_class=None, index_name=None):
        """Return a search query that will search this model.

        Args:
            * ids_only: Whether to return just the IDs of the documents in the
                search index, or return the full documents themselves.
            * document_class: The document class to instantiate the results
                from the query with.
            * index_name: The name of the index to search

        Returns:
            A `search.SearchQuery` bound to the given document class and index.
        """
        # If there's no index name given, use the default
        if not index_name:
            index_name = get_default_index_name(model_class)

        if not document_class:
            document_class = registry[model_class]

        index = Index(index_name)
        return index.search(document_class=document_class, ids_only=ids_only)

    if not getattr(model_class, "search_query", None):
        model_class.search_query = classmethod(search_query)


def searchable(document_class, index_name=None, rank=None):
    """Make the decorated model searchable. Can be used to decorate a model
    multiple times should that model need to be indexed in several indexes.

    Adds receivers for the model's `post_save` and `pre_delete` signals that
    index and unindex that instance, respectfully, whenever it's saved or
    deleted.

    Args:
        document_class: The document class to index instances of this model
            with. When indexing an instance, a document class will be
            instantiated and then have its `build` method called, with the
            instance as an argument.
        index_name: The name of the search index to add the documents to. It's
            valid for the same object to be added to multiple indexes.
            Default: lowercase {app_label}_{model_name}.
        rank: Either:

            * The name of a field on the model instance
            * The name of a method taking no args on the model instance
            * A callable taking no args

            that will return the rank to use for that instance's document in
            the search index.
    """
    if isinstance(document_class, basestring):
        document_class = import_string(document_class)

    def decorator(model_class):
        index = Index(index_name or get_default_index_name(model_class))

        registry[model_class] = document_class

        connect_signals(model_class, document_class, index.name, rank=rank)
        add_search_query_method(model_class)
        model_class._search_meta = (index.name, document_class, rank)
        return model_class

    return decorator
