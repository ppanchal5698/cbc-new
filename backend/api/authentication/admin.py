"""
User administration — and the activation gate.

Signup creates an inactive account; this is where it becomes a usable one. That is
deliberately a human action in a screen an admin already has, rather than an
approval API: it happens a handful of times, by someone who is already
authenticated as staff, and an endpoint for it would be one more public surface
guarding the same decision.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("email", "full_name", "role", "job_title", "is_active", "date_joined")
    # is_active first: the pending-approval queue is the reason anyone opens this
    # screen, and it should be one click away rather than a search.
    list_filter = ("is_active", "role", "is_superuser")
    search_fields = ("email", "full_name", "job_title")
    ordering = ("-date_joined",)
    readonly_fields = ("date_joined", "last_login")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("full_name", "job_title", "phone")}),
        # is_staff is absent on purpose: the model derives it from role, and a
        # second editable control for the same fact is how the two drift apart.
        ("Access", {"fields": ("is_active", "role", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    # DjangoUserAdmin's default add form asks for a username this model does not
    # have, so the create screen is redeclared around email.
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "full_name", "password1", "password2", "is_active", "role"),
        }),
    )

    actions = ["activate_users"]

    @admin.action(description="Activate selected users (grants access to client data)")
    def activate_users(self, request, queryset):
        activated = queryset.filter(is_active=False).update(is_active=True)
        self.message_user(request, f"{activated} user(s) activated.")
