"""
Signup, login, logout, profile, and password change (C3 / ADR-0004).

Three rules run through all of it.

**A new account cannot sign in.** Signup records a request for access; an admin
grants it. The system holds client bid drawings and CBC's cost and margin data,
and self-service is not an appropriate way to obtain either.

**Failures do not distinguish.** Wrong password, no such account, and correct
password on an account that does not exist all answer identically. The one
exception is a *correct* credential on an inactive account, which is told to wait —
that reveals nothing to someone who already proved they hold the password.

**Public endpoints opt out explicitly.** Everything else inherits
``IsAuthenticated`` from the DRF defaults, so an endpoint is protected unless
someone deliberately wrote otherwise on it.
"""

from django.contrib.auth import (
    authenticate,
    get_user_model,
    login,
    logout,
    update_session_auth_hash,
)
from django.db import IntegrityError, transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import (
    ChangePasswordSerializer,
    LoginSerializer,
    ProfileSerializer,
    SignupSerializer,
)

User = get_user_model()

#: One string for every way a credential can be wrong. Two messages are two bits
#: of information about which accounts exist.
INVALID_CREDENTIALS = "Email or password is incorrect."

#: Said only to someone who supplied the right password, so it discloses nothing
#: they did not already know.
AWAITING_APPROVAL = (
    "This account is awaiting approval. An administrator has to activate it "
    "before you can sign in."
)

#: Identical whether or not the email was already registered.
SIGNUP_RECEIVED = (
    "Signup received. An administrator has to approve the account before you can "
    "sign in; you will not be able to log in until then."
)


def _authenticate(request, email: str, password: str):
    """
    Return ``(user, error_response)``. Exactly one is ever non-None.

    ``authenticate()`` returns None for an inactive user as well as for a wrong
    password, so the two are separated here by re-checking the password against
    the row — which is safe to do only *after* the password has been proven.
    """
    user = authenticate(request, username=email, password=password)
    if user is not None:
        return user, None

    inactive = User.objects.filter(email__iexact=email, is_active=False).first()
    if inactive is not None and inactive.check_password(password):
        return None, Response(
            {"detail": AWAITING_APPROVAL}, status=status.HTTP_403_FORBIDDEN
        )

    return None, Response(
        {"detail": INVALID_CREDENTIALS}, status=status.HTTP_401_UNAUTHORIZED
    )


class SignupView(APIView):
    """Request an account. Creates it inactive; an admin activates it."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_scope = "signup"

    @extend_schema(request=SignupSerializer, responses={202: None})
    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            with transaction.atomic():
                User.objects.create_user(
                    email=data["email"],
                    password=data["password"],
                    full_name=data.get("full_name", ""),
                    job_title=data.get("job_title", ""),
                    # Explicit, though create_user already defaults to it. This is
                    # the line a reader comes here to check.
                    is_active=False,
                )
        except IntegrityError:
            # The email is taken. Answering differently here would turn this
            # endpoint into a way to enumerate who works at CBC.
            pass

        return Response({"detail": SIGNUP_RECEIVED}, status=status.HTTP_202_ACCEPTED)


class LoginView(APIView):
    """Start a session. Refuses accounts an admin has not activated."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_scope = "login"

    @extend_schema(request=LoginSerializer, responses={200: ProfileSerializer})
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user, error = _authenticate(
            request, serializer.validated_data["email"], serializer.validated_data["password"]
        )
        if error is not None:
            return error

        login(request, user)
        return Response(ProfileSerializer(user).data)


class LogoutView(APIView):
    """End the session server-side, not merely on the client."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={204: None})
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TokenView(APIView):
    """
    Issue a DRF token for scripts, CI, and the pipeline harness.

    Separate from login because the two have different lifetimes: a session ends
    when the browser closes, a token lives until it is deleted.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_scope = "token"

    @extend_schema(request=LoginSerializer, responses={200: None})
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user, error = _authenticate(
            request, serializer.validated_data["email"], serializer.validated_data["password"]
        )
        if error is not None:
            return error

        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key})


class MeView(APIView):
    """Read or edit your own profile. Never anyone else's."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: ProfileSerializer})
    def get(self, request):
        return Response(ProfileSerializer(request.user).data)

    @extend_schema(request=ProfileSerializer, responses={200: ProfileSerializer})
    def patch(self, request):
        serializer = ProfileSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class ChangePasswordView(APIView):
    """Change your own password, proving you know the current one."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "password"

    @extend_schema(request=ChangePasswordSerializer, responses={204: None})
    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)

        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password"])

        # Changing a password rotates the session hash, which would otherwise log
        # the user out of the very request they are making.
        update_session_auth_hash(request, request.user)

        # Every issued token was minted against the old password. Leaving them
        # valid means a password change does not actually revoke anything.
        Token.objects.filter(user=request.user).delete()

        return Response(status=status.HTTP_204_NO_CONTENT)
