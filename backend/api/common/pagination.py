"""Pagination defaults (§9 Risk R9)."""

from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """
    Default for every list endpoint.

    ``doc_elements`` reaches tens of thousands of rows per bid set even after
    triage. An unpaginated list over that table is a denial of service against our
    own API host, so the page size is capped rather than merely defaulted.
    """

    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 1000


class ElementPagination(StandardPagination):
    """Larger pages for the source viewer, which fetches a whole page of elements."""

    page_size = 500
    max_page_size = 5000
