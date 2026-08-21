"""
The user model (C3 / ADR-0004, §11.2).

**Django auth is the authorisation and audit boundary.** Cognito is deferred, not
absent by oversight: it solves a problem this build does not have — ten known
internal users, already authenticated — and if SSO is later required an OIDC
provider sits *in front of* this model rather than replacing it.

Two things here are load-bearing:

**Email is the identity.** This domain already addresses people by email —
``Project.initiator_email`` routes the finished quote, and every ``feedback`` row
names who changed a value. A separate username would be a second name for the same
person and an invitation for the two to disagree.

**A new account is inactive.** Signup is open; *access* is not. The system holds
client bid drawings and CBC's cost and margin data, so an account that can read it
exists only once someone with admin access says so.
"""

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models

from shared.enums import Role


class UserManager(BaseUserManager):
    """
    Creates users keyed by email rather than username.

    Django's default manager requires a username, which this model does not have.
    """

    use_in_migrations = True

    def _create(self, email: str, password: str | None, **extra):
        if not email:
            raise ValueError("an email address is required")
        user = self.model(email=self.normalize_email(email), **extra)
        # set_password even when password is None: that stores an unusable hash,
        # which is what an admin-created or SSO-fronted account should carry. A
        # blank password field would be a login with no secret.
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra):
        """
        A normal account. **Inactive unless the caller says otherwise.**

        The default is the safe one on purpose: any code path that forgets to pass
        ``is_active`` produces an account that cannot sign in, rather than one that
        silently can.
        """
        extra.setdefault("role", Role.ESTIMATOR.value)
        extra.setdefault("is_superuser", False)
        extra.setdefault("is_active", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra):
        extra.setdefault("role", Role.ADMIN.value)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_active", True)
        if extra["role"] != Role.ADMIN.value or not extra["is_superuser"]:
            raise ValueError("a superuser must hold the ADMIN role and is_superuser")
        return self._create(email, password, **extra)


class User(AbstractUser):
    """
    An estimator, or an admin who is also allowed to steward the reference data.

    Two roles, and no permission table behind them. Django's group and per-object
    permission machinery is real and unused here on purpose: with two roles and one
    axis of difference, a ``role`` column answers every question the codebase asks,
    and ``user.is_admin`` reads at a glance where a permission lookup does not.
    """

    # AbstractUser's own fields, removed rather than left to rot beside their
    # replacements. Two places to store a name is one place for them to disagree.
    username = None
    first_name = None
    last_name = None

    email = models.EmailField(
        unique=True,
        help_text="The login identifier, and where a finished quote is sent.",
    )
    full_name = models.CharField(
        max_length=150,
        blank=True,
        help_text="Shown on quotes and against every correction in the audit trail.",
    )
    job_title = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=40, blank=True)

    role = models.CharField(
        max_length=20,
        choices=Role.choices(),
        default=Role.ESTIMATOR.value,
        help_text="ADMIN also stewards pricing and catalog data, and may open /admin/.",
    )

    USERNAME_FIELD = "email"
    #: Everything else is optional. createsuperuser prompts for email and password.
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    class Meta:
        db_table = "authentication_user"
        ordering = ["email"]

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN.value

    def save(self, *args, **kwargs):
        """
        Keep ``is_staff`` derived from ``role``.

        Django reads ``is_staff`` to decide who may open the admin site, and this
        model has a second field describing the same thing. Two writable sources
        for one fact drift — someone ticks the admin-site box without changing the
        role, and a user has admin-site access while every API permission check
        says estimator. Role is the source of truth; the admin form hides
        ``is_staff`` for the same reason.
        """
        self.is_staff = self.is_admin
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.full_name or self.email

    def get_full_name(self) -> str:
        """Django calls this from admin and templates; keep one source of truth."""
        return self.full_name or self.email

    def get_short_name(self) -> str:
        return self.full_name.split(" ")[0] if self.full_name else self.email
