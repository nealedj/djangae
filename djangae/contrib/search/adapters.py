import logging
from django.db.models import Q

from search.ql import Q as SQ


def resolve_filter_value(v):
    """Resolve a filter value to one that the search API can handle

    We can't pass model instances for example.
    """

    return getattr(v, 'pk', v)


def model_q_to_search_q(_q):
    """Transform a `django.db.model.Q` tree to `search.ql.Q` tree.

    TODO: handle negation
    """

    if type(_q) is tuple:
        k, v = _q
        return (k, resolve_filter_value(v))

    if not _q.children:
        return None

    q = SQ()
    q.conn = _q.connector
    q.children = filter(
        lambda x: x is not None,
        map(model_q_to_search_q, _q.children)
    )
    q.inverted = _q.negated

    if not q.children:
        return None

    # TODO: handle negation?

    return q


class SearchQueryAdapter(object):
    """Adapter class to wrap a `search.query.SearchQuery` instance to behaves
    like a Django queryset.

    We only implement 'enough' to allow it's use within a rest_framework
    viewset and django_filter Filterset.
    """

    def __init__(self, query=None, model=None, queryset=None, _is_none=False):
        self._is_none = _is_none
        self._query = query
        self._queryset = queryset
        self.model = None if queryset is None else queryset.model

    def _clone(self):
        return self.__class__(
            model=self.model,
            queryset=self._queryset,
            _is_none=self._is_none)

    def _transform_filters(self, *args, **kwargs):
        """Normalize a set of filter kwargs intended for Django queryset to
        those than can be used with a search queryset

        Returns tuple of (args, kwargs) to pass directly to SearchQuery.filter
        """

        _args = [
            model_q_to_search_q(_q) if type(_q) is Q else _q
            for _q in args
        ]
        _kwargs = {k: resolve_filter_value(v) for k, v in kwargs.iteritems()}

        return _args, _kwargs

    def as_model_objects(self):
        """Get the IDs in the order they came back from the search API...
        """
        doc_pks = [int(doc.pk) for doc in self]
        results = (
            self.model.objects
            .filter(id__in=doc_pks)
            .prefetch_related(*self._queryset._prefetch_related_lookups)
        )

        # Since we do pk__in to get the objects from the datastore, we lose
        # any ordering there was. To recreate it, we have to manually order
        # the list back into whatever order the pks from the search API were in
        objects_by_pk = {o.pk: o for o in results}
        results = []
        for pk in doc_pks:
            if pk not in objects_by_pk:
                logging.warning(
                    "%s with PK %d doesn't exist, but search returned it!" % (self.model.__name__, pk),
                )
                continue

            yield objects_by_pk[pk]

    def all(self):
        clone = self._clone()
        clone._query = self._query._clone()
        return clone

    def __len__(self):
        return 0 if self._is_none else self._query.__len__()

    def __iter__(self):
        if self._is_none:
            return iter([])
        else:
            return self._query.__iter__()

    def __getitem__(self, s):
        clone = self._clone()
        clone._query = self._query.__getitem__(s)
        return clone

    def filter(self, *args, **kwargs):
        args, kwargs = self._transform_filters(*args, **kwargs)
        args = args or []

        clone = self._clone()
        clone._query = self._query.filter(*args, **kwargs)

        return clone

    def none(self):
        clone = self._clone()
        clone._is_none = True
        clone._query = self._query._clone()
        return clone

    def count(self):
        return 0 if self._is_none else len(self._query)

    def order_by(self, *fields):
        qs = self._query.order_by(*fields)
        clone = self._clone()
        clone._query = qs
        return clone

