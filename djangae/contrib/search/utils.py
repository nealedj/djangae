import logging
import operator
import threading

from django.conf import settings
from django.db.models.lookups import Lookup

try:
    from text_unidecode import unidecode
except ImportError:
    HAS_UNIDECODE = False
else:
    HAS_UNIDECODE = True

from search.ql import Q

from .adapters import SearchQueryAdapter


status = threading.local()


MAX_RANK = 2 ** 31


def get_ascii_string_rank(string, max_digits=9):
    """Convert a string into a number such that when the numbers are sorted
    they maintain the lexicographic sort order of the words they represent.

    The number of characters in the string for which lexicographic order will
    be maintained depends on max_digits. For the default of 9, the number of
    chars that the order is maintained for is 5.

    Unfortunately this basically means:

    >>> get_ascii_string_rank("Python") == get_ascii_string_rank("Pythonic")
    True

    when obviously it'd be better if the rank for "Pythonic" was > than the
    rank for "Python" since "Pythonic" is alphabetically after "Python".
    """
    # Smallest ordinal value we take into account
    smallest_ord = ord(u"A")
    # Ord value to use for punctuation - we define punctuation as ordering after
    # all letters in the alphabet
    punctuation_ord = smallest_ord - 1
    # Offset to normalize the actual ord value by. 11 is taken off because
    # otherwise the values for words starting with 'A' would start with '00'
    # which would be ignored when cast to an int
    offset = smallest_ord - 11
    # Fn to get the normalized ordinal
    get_ord = lambda c: (ord(c) if c.isalpha() else punctuation_ord) - offset
    # Padding for the string if it's shorter than `max_digits`
    padding = chr(punctuation_ord) * max_digits

    if HAS_UNIDECODE:
        # And parse it with unidecode to get rid of non-ascii characters
        string = unidecode(string)
    else:
        logging.warning(
            'text_unidecode package not found. If a string with non-ascii chars '
            'is used for a document rank it may result in unexpected ordering'
        )

    # Get the ordinals...
    ords = [get_ord(c) for c in (string + padding)]
    # Concat them, making sure they're all 2 digits long
    joinable = [str(o).zfill(2) for o in ords]
    # Cast back to an int, making sure it's at at most `max_digits` long
    return int("".join(joinable)[:max_digits])


def get_rank(instance, rank=None):
    """Get the rank with which this instance should be indexed.

    Args:
        instance: A Django model instance
        rank: Either:

            * The name of a field on the model instance
            * The name of a method taking no args on the model instance
            * A callable taking no args

            that will return the rank to use for that instance's document in
            the search index.

    Returns:
        The rank value, between 0 and 2**63
    """
    desc = True

    if not rank:
        return rank

    if callable(rank):
        rank = rank()
    else:
        desc = rank.startswith("-")
        rank = rank[1:] if desc else rank

        rank = getattr(instance, rank)
        if callable(rank):
            rank = rank()

    if isinstance(rank, basestring):
        rank = get_ascii_string_rank(rank)

    # The Search API returns documents in *descending* rank order by default,
    # so reverse if the rank is to be ascending
    return rank if desc else MAX_RANK - rank


def get_default_index_name(model_class):
    """Get the default search index name for the given model"""
    return "{0.app_label}_{0.model_name}".format(model_class._meta)


def get_uid(model_class, document_class, index_name):
    """Make the `dispatch_uid` for this model, document and index combination.

    Returns:
        A string UID for use as the `dispatch_uid` arg to `@receiver` or
        `signal.connect`
    """
    return "{index_name}.{model_class}.{document_class}".format(
        index_name=index_name,
        model_class=model_class.__name__,
        document_class=document_class.__name__
    )


def indexing_is_enabled():
    """
    Returns:
        Whether or not search indexing/deleting is enabled.
    """
    default = getattr(
        settings,
        "SEARCH_INDEXING_ENABLED_BY_DEFAULT",
        True
    )
    return getattr(status, "_is_enabled", default)


def _disable():
    """Disable search indexing globally for this thread"""
    status._is_enabled = False


def _enable():
    """Enable the search indexing globally for this thread"""
    status._is_enabled = True


class DisableIndexing(object):
    """A context manager/callable that disables indexing. If used in a `with`
    statement, indexing will be disabled temporarily and then restored to
    whatever state it was before.
    """
    def __enter__(self):
        if not hasattr(self, "previous_state"):
            self.previous_state = indexing_is_enabled()
        _disable()

    def __call__(self):
        self.previous_state = indexing_is_enabled()
        _disable()
        return self

    def __exit__(self, *args, **kwargs):
        _enable() if self.previous_state is True else _disable()


class EnableIndexing(object):
    """A context manager/callable that enables indexing. If used in a `with`
    statement, indexing will be enabled temporarily and then restored to
    whatever state it was before.
    """
    def __enter__(self):
        if not hasattr(self, "previous_state"):
            self.previous_state = indexing_is_enabled()
        _enable()

    def __call__(self):
        self.previous_state = indexing_is_enabled()
        _enable()
        return self

    def __exit__(self, *args, **kwargs):
        _enable() if self.previous_state is True else _disable()


# Context managers for use with the `with` statement, to temporarily disable/
# enable search indexing
disable_indexing = DisableIndexing()
enable_indexing = EnableIndexing()



def get_filters_from_queryset(queryset, where_node=None):
    """Translates django queryset filters into a nested dict of tuples

    example:
    queryset = Profile.objects.filter(given_name='pete').filter(Q(email='1@thing.com') | Q(email='2@thing.com'))
    get_filters_from_queryset(queryset)
    returns:
    {
        u'children': [
                (u'given_name', u'exact', 'pete'),
                {
                    u'children': [
                        (u'email', u'exact', '1@thing.com'),
                        (u'email', u'exact', '2@thing.com')
                    ],
                    u'connector': u'OR'
                }
            ],
        u'connector': u'AND'
    }
    """
    where_node = where_node or queryset.query.where

    node_filters = {
        u'connector': unicode(where_node.connector),
    }

    children = []

    for node in where_node.children:
        # Normalize expressions which are an AND with a single child and pull the
        # use the child node as the expression instead.
        # This happens if you add querysets together.
        if getattr(node, 'connector', None) == 'AND' and len(node.children) == 1:
            node = node.children[0]

        if isinstance(node, Lookup):  # Lookup
            children.append(build_lookup(node))

        else:  # WhereNode
            children.append(
                get_filters_from_queryset(
                    queryset,
                    node,
                )
            )
    node_filters[u'children'] = children
    return node_filters


def filters_to_search_query(filters, model, query=None):
    """Convert a list of nested lookups filters (a result of get_filters_from_queryset)
    into a SearchQuery objects."""
    search_query = query or model.search_query()
    connector = filters['connector']
    children = filters['children']

    q_objects = None

    for child in children:
        if isinstance(child, tuple):
            q = Q(
                **{
                    "{}__{}".format(child[0], child[1]): child[2]
                }
            )
            operator_func = getattr(operator, connector.lower() + '_', 'and_')
            q_objects = operator_func(q_objects, q) if q_objects else q

        else:
            search_query = filters_to_search_query(child, model, query=search_query)

    if q_objects is not None:
        # This is essentially a copy of the logic in Query.add_q
        # The trouble is that add_q always ANDs added Q objects but in this case
        # we want to specify the connector ourselves
        if search_query.query._gathered_q is None:
            search_query.query._gathered_q = q_objects
        else:
            search_query.query._gathered_q = getattr(
                search_query.query._gathered_q,
                '__{}__'.format(connector.lower())
            )(q_objects)

    return search_query


def build_lookup(node):
    """Converts Django Lookup into a single tuple
    or a list of tuples if the lookup_name is IN

    example for lookup_name IN and rhs ['1@thing.com', '2@thing.com']:
    {
        u'connector': u'OR',
        u'children': [
            (u'email', u'=', u'1@thing.com'),
            (u'email', u'=', u'2@thing.com')
        ]
    }

    example for lookup_name that's not IN (exact in this case) and value '1@thing.com':
    (u'email', u'=', u'1@thing.com')
    """
    target = unicode(node.lhs.target.name)
    lookup_name = unicode(node.lookup_name)

    # convert "IN" into a list of "="
    if lookup_name.lower() == u'in':
        return {
            u'connector': u'OR',
            u'children': [
                (
                    target,
                    u'exact',
                    value,
                )
                for value in node.rhs
            ]
        }

    return (
        target,
        lookup_name,
        node.rhs,
    )


def django_qs_to_search_qs(queryset):
    """Converts django queryset into search queryset that acts just like the django one,
    unless it already is of SearchQueryAdapter type"""

    # do nothing if already converted
    if isinstance(queryset, SearchQueryAdapter):
        return queryset

    filters = get_filters_from_queryset(queryset)

    search_query = filters_to_search_query(filters, queryset.model)

    return SearchQueryAdapter(search_query, queryset=queryset)
