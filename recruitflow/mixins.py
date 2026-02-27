"""
recruitflow/mixins.py

Shared view mixins for reuse across all app views.
"""

_PER_PAGE_OPTIONS = [10, 25, 50, 100]
_DEFAULT_PER_PAGE = 25


class PaginationMixin:
    """
    Drop-in mixin for ListView subclasses.
    Reads `per_page` from the GET param (validated against allowed options)
    and injects `per_page` + `per_page_options` into the template context.
    """

    def _get_per_page(self) -> int:
        try:
            value = int(self.request.GET.get("per_page", _DEFAULT_PER_PAGE))
        except (ValueError, TypeError):
            value = _DEFAULT_PER_PAGE
        return value if value in _PER_PAGE_OPTIONS else _DEFAULT_PER_PAGE

    def get_paginate_by(self, queryset):
        return self._get_per_page()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["per_page"] = self._get_per_page()
        ctx["per_page_options"] = _PER_PAGE_OPTIONS
        return ctx
