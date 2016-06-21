from django.core import paginator as django_paginator
from django.utils import six

from rest_framework import exceptions, pagination as drf_pagination

from .adapters import SearchQueryAdapter


class IsSearchinMixin(object):
    def is_searching(self):
        return isinstance(self.object_list, SearchQueryAdapter)


class SearchPage(django_paginator.Page, IsSearchinMixin):
    _objects = None

    def load_objects(self, lazy=True):
        if self._objects is None:
            if self.is_searching():
                self._objects = self.object_list.as_model_objects()
            else:
                self._objects = super(SearchPage, self).__iter__()

        # force evaluation of objects in the page
        if not lazy and not isinstance(self._objects, list):
            self._objects = list(o for o in self._objects)

    def __iter__(self):
        self.load_objects()
        return iter(self._objects)


class SearchPaginator(django_paginator.Paginator, IsSearchinMixin):
    _page = None

    def _get_page(self, *args, **kwargs):
        return SearchPage(*args, **kwargs)

    def validate_number(self, number):
        """Override default handling to remove the extra search query
        """
        try:
            number = int(number)
        except (TypeError, ValueError):
            raise django_paginator.PageNotAnInteger('That page number is not an integer')
        if number < 1:
            raise django_paginator.EmptyPage('That page number is less than 1')
        return number

    def page(self, number):
        assert not self.orphans, "SearchPaginator does not support orphans"
        number = self.validate_number(number)
        bottom = (number - 1) * self.per_page
        top = bottom + self.per_page
        self._page = self._get_page(self.object_list[bottom:top], number, self)

        # force evaluation at this point as we need to get the counts from the meta
        self._page.load_objects(lazy=False)

        return self._page

    def _get_count(self):
        # if we're searching then we can get the count from the
        # sliced objects list within the page
        if self.is_searching() and self._page is not None:
            return self._page.object_list.count()

        return super(SearchPaginator, self)._get_count()
    count = property(_get_count)


class SearchPageNumberPagination(drf_pagination.PageNumberPagination):
    def paginate_queryset(self, queryset, request, view=None):
        """
        Override the DRF paginator purely in order to hook up our SearchPaginator in
        place of the DjangoPaginator
        """
        self._handle_backwards_compat(view)

        page_size = self.get_page_size(request)
        if not page_size:
            return None

        paginator = SearchPaginator(queryset, page_size)
        page_number = request.query_params.get(self.page_query_param, 1)
        if page_number in self.last_page_strings:
            page_number = paginator.num_pages

        try:
            self.page = paginator.page(page_number)
        except django_paginator.InvalidPage as exc:
            msg = u'Invalid page: {message}.'.format(
                message=six.text_type(exc)
            )
            raise exceptions.NotFound(msg)

        if paginator.num_pages > 1 and self.template is not None:
            # The browsable API should display pagination controls.
            self.display_page_controls = True

        self.request = request
        return list(self.page)
