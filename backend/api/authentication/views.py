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

from common import mail
from django.conf import settings
from django.contrib.auth import (
    authenticate,
    get_user_model,
    login,
    logout,
    password_validation,
    update_session_auth_hash,
)
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import (
    ChangePasswordSerializer,
    LoginSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
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

#: Identical whether or not the address has an account. Same reasoning as signup.
RESET_REQUESTED = (
    "If that address has an active account, a password reset link is on its way. "
    "The link expires in one hour."
)

#: A token is single-use and short-lived, so an expired or replayed one is an
#: ordinary event, not an error worth distinguishing from a forged one.
RESET_LINK_INVALID = "This reset link is invalid or has expired. Request a new one."

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


class PasswordResetRequestView(APIView):
    """
    Ask for a reset link.

    Answers identically for a registered address, an unknown one, and an account
    still awaiting approval. Anything else turns this endpoint into a way to test
    whether a given person works at CBC — and it is the one endpoint an attacker
    can hit without any credential at all.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_scope = "password_reset"

    @extend_schema(request=PasswordResetRequestSerializer, responses={202: None})
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        # Active only. An unapproved account has no access to reset, and mailing
        # one would confirm to a stranger that the address is registered.
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user is not None:
            token = default_token_generator.make_token(user)
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            link = settings.PASSWORD_RESET_URL.format(uid=uid, token=token)

            mail.send(
                subject="Reset your CBC Copilot password",
                body="\n".join(
                    [
                        f"Hello {user.get_short_name()},",
                        "",
                        "Someone asked to reset the password for this CBC Copilot "
                        "account. Open the link below to choose a new one:",
                        "",
                        link,
                        "",
                        "The link works once and expires in one hour.",
                        "",
                        "If this was not you, you can ignore this message — nothing "
                        "has changed and your current password still works.",
                        "",
                    ]
                ),
                to=user.email,
            )

        return Response({"detail": RESET_REQUESTED}, status=status.HTTP_202_ACCEPTED)


class PasswordResetConfirmView(APIView):
    """
    Redeem a reset link.

    Django's ``default_token_generator`` does the work: the token is derived from
    the user's current password hash and ``last_login``, so it stops working the
    moment either changes. That makes it single-use without a table to track, and
    invalidates every outstanding link as soon as one is redeemed.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_scope = "password_reset"

    @extend_schema(request=PasswordResetConfirmSerializer, responses={204: None})
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = self._user_from_uid(data["uid"])
        if user is None or not default_token_generator.check_token(user, data["token"]):
            # One message for a malformed uid, an unknown user, an inactive one,
            # a forged token and an expired token alike.
            return Response(
                {"detail": RESET_LINK_INVALID}, status=status.HTTP_400_BAD_REQUEST
            )

        # Validated only after the token proves the caller owns the mailbox, so the
        # error messages cannot be used to probe the password policy anonymously.
        try:
            password_validation.validate_password(data["new_password"], user)
        except DjangoValidationError as exc:
            return Response(
                {"new_password": list(exc.messages)}, status=status.HTTP_400_BAD_REQUEST
            )

        user.set_password(data["new_password"])
        user.save(update_fields=["password"])

        # Same reasoning as change-password: a token minted against the old
        # password must not outlive it.
        Token.objects.filter(user=user).delete()

        return Response(status=status.HTTP_204_NO_CONTENT)

    @staticmethod
    def _user_from_uid(uid: str):
        try:
            pk = force_str(urlsafe_base64_decode(uid))
        except (TypeError, ValueError, OverflowError, UnicodeDecodeError):
            return None
        return User.objects.filter(pk=pk, is_active=True).first()
