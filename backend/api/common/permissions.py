"""
Role-based access control (§11.2, NFR-10, Risk R5).

**Every class here requires authentication itself, and that is not redundant.**
Setting ``permission_classes`` on a viewset *replaces* DRF's project-wide default
rather than adding to it — so a class that only asked "is this an admin?" would
answer True for an anonymous caller on a GET, and quietly open reference data and
project listings to the internet.

That regression was written and caught by the endpoint-coverage test within
minutes. Requiring authentication inside each class means a single-entry
``permission_classes = [IsAdminOrReadOnly]`` is safe on its own, instead of
depending on every call site remembering to pair it with ``IsAuthenticated``.

**Reads are open to every authenticated user.** An estimator cannot price a door
without seeing margin bands and vendor multipliers, and hiding reference data from
the people who work with it would produce workarounds rather than security. What
these classes protect is *writes* — and the destructive verb specifically.
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission

#: Everything except GET, HEAD and OPTIONS.
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def _is_authenticated(request) -> bool:
    return bool(request.user and request.user.is_authenticated)


def _is_admin(request) -> bool:
    """
    True when the caller is signed in AND holds the ADMIN role.

    ``getattr`` rather than a bare attribute: DRF may hand these classes an
    ``AnonymousUser``, which has no ``is_admin``, and a permission class that
    raises turns a 403 into a 500.
    """
    return _is_authenticated(request) and bool(getattr(request.user, "is_admin", False))


class IsAdmin(BasePermission):
    """Admins only, for every method including reads."""

    message = "This action requires the ADMIN role."

    def has_permission(self, request, view) -> bool:
        return _is_admin(request)


class IsAdminOrReadOnly(BasePermission):
    """
    Anyone signed in may read; only an admin may change.

    This is what guards the reference library — margin bands, vendor multipliers,
    tax rates, finish codes, throat depths, catalog items. Until now any account
    could rewrite CBC's margins, and a quote priced from a silently altered
    multiplier is wrong in a way nothing downstream can detect.

    It is also the mechanism for the data steward NFR-10 and Risk R5 call
    unnamed. The role decides who *may*; CBC still has to say who *does*.
    """

    message = "Changing reference data requires the ADMIN role."

    def has_permission(self, request, view) -> bool:
        if not _is_authenticated(request):
            return False
        if request.method in SAFE_METHODS:
            return True
        return _is_admin(request)


class IsAdminForDestroy(BasePermission):
    """
    Estimators create, edit and approve. Only an admin deletes.

    Deleting a project or a quote removes the record a citation, an audit trail and
    possibly a sent price all hang off. That is a different kind of act from
    editing one, and the asymmetry is deliberate: the everyday work stays
    frictionless and the irreversible verb does not.
    """

    message = "Deleting this requires the ADMIN role."

    def has_permission(self, request, view) -> bool:
        if not _is_authenticated(request):
            return False
        if request.method != "DELETE":
            return True
        return _is_admin(request)
