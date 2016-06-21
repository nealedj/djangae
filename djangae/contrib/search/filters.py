import string as string_module


QUOTES = (u"'", u'"')
ALLOWED_PUNCTUATIONS = (u"_", u"-")


class KeywordSearch(object):
    """A filter backend that executes a search query on an endpoint that is
    searchable. Meant to be integrated with views that extend from SearchMixin.
    """
    def __init__(self, get_param="search"):
        self.get_param = get_param

    def __call__(self):
        return self

    def filter_queryset(self, request, queryset, view):
        if getattr(view, "is_searching", lambda: False)():
            query = request.GET.get(self.get_param)
            if query:
                return filter_search(queryset, query)
        return queryset


def is_wrapped_in_quotes(string):
    """Check to see if the given string is quoted"""
    return string[0] in QUOTES and string[0] == string[-1]


def strip_surrounding_quotes(string):
    """Strip quotes from the ends of the given string"""
    if not is_wrapped_in_quotes(string):
        return string

    to_strip = string[0]
    return string.strip(to_strip)


def strip_special_search_characters(string):
    """Some punctuation characters break the Search API's query parsing if
    they're not escaped. Some punctuation characters break even if escaped.
    There's no documentation as to which characters should be escaped and which
    should be completely removed, so to stop _all_ errors, we remove all common
    punctuation to avoid brokenness.
    """
    for char in string_module.punctuation:
        if char not in ALLOWED_PUNCTUATIONS:
            string = string.replace(char, u"")
    return string


def strip_multi_value_operators(string):
    """The Search API will parse a query like `PYTHON OR` as an incomplete
    multi-value query, and raise an error as it expects a second argument
    in this query language format. To avoid this we strip the `AND` / `OR`
    operators tokens from the end of query string. This does not stop
    a valid multi value query executing as expected."""
    # the search API source code lists many operators in the tokenNames
    # iterable, but it feels cleaner for now to explicitly name only the ones
    # we are interested in here
    operator_tokens = ("OR", "AND")
    last_word_in_string = string.strip().split()[-1]
    if last_word_in_string in operator_tokens:
        return string.rstrip(last_word_in_string)
    return string


def filter_search(queryset, value):
    exact = False

    if not value:
        return queryset

    # If the user wrapped their entire search in quotes, there's a good chance
    # they're looking for that exact string, so switch to using implied
    # `__exact` filtering, and remove the surrounding quotes
    if is_wrapped_in_quotes(value):
        exact = True
        value = strip_surrounding_quotes(value)

    value = strip_special_search_characters(value)
    value = strip_multi_value_operators(value)

    if exact:
        return queryset.filter(corpus=value)

    # ...otherwise, search normally
    return queryset.filter(corpus__contains=value)

