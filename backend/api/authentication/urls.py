"""
Auth routes, mounted at /api/auth/ (§11.2).

Plain APIViews rather than a router: these are actions, not a resource, and a
ViewSet would invent list and detail routes over user accounts that nothing should
have.
"""

from django.urls import path

from .views import (
    ChangePasswordView,
    LoginView,
    LogoutView,
    MeView,
    SignupView,
    TokenView,
)

urlpatterns = [
    path("auth/signup/", SignupView.as_view(), name="signup"),
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("auth/token/", TokenView.as_view(), name="token"),
    path("auth/me/", MeView.as_view(), name="me"),
    path("auth/change-password/", ChangePasswordView.as_view(), name="change-password"),
]
