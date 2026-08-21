"""
Signup, profile, and password serializers.

The recurring concern here is what a response *reveals*. An endpoint that answers
differently for a known and an unknown email is an account-enumeration oracle, and
a profile serializer that accepts more fields than it should is a
privilege-escalation route. Both are handled by construction rather than by
remembering.
"""

from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

User = get_user_model()

#: The only fields a user may change about themselves.
#:
#: Whitelisted, never blacklisted. A blacklist grows a hole the moment a field is
#: added to the model — and the field most likely to be added is exactly the kind
#: that should not be self-assignable.
EDITABLE_PROFILE_FIELDS = ("full_name", "job_title", "phone")


def _validate_password(password: str, user=None) -> None:
    """Run Django's configured validators and re-raise as a DRF error."""
    try:
        password_validation.validate_password(password, user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(list(exc.messages)) from exc


class ProfileSerializer(serializers.ModelSerializer):
    """
    The caller's own profile.

    ``email`` is readable and not writable: it is the login identity and it routes
    finished quotes, so changing it is an admin action, not a self-service one.
    ``is_active`` and ``is_staff`` are readable so a client can render state, and
    read-only so a PATCH cannot grant privilege.
    """

    class Meta:
        model = User
        fields = (
            "id", "email", "full_name", "job_title", "phone",
            "is_active", "is_staff", "date_joined", "last_login",
        )
        read_only_fields = ("id", "email", "is_active", "is_staff", "date_joined", "last_login")

    def update(self, instance, validated_data):
        # Belt and braces with read_only_fields above. A serializer field list is
        # edited by people; this loop cannot be edited into permissiveness by
        # accident.
        for field in list(validated_data):
            if field not in EDITABLE_PROFILE_FIELDS:
                validated_data.pop(field)
        return super().update(instance, validated_data)


class SignupSerializer(serializers.Serializer):
    """
    A request for access — not the granting of it.

    Deliberately **not** a ModelSerializer with ``unique=True`` on email: that
    returns "user with this email already exists", which tells an anonymous caller
    exactly which addresses have accounts. Uniqueness is enforced by the database
    and handled silently in the view.
    """

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    full_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    job_title = serializers.CharField(max_length=100, required=False, allow_blank=True)

    def validate_password(self, value: str) -> str:
        _validate_password(value)
        return value


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})


class ChangePasswordSerializer(serializers.Serializer):
    """
    Requires the current password.

    Without that check, anyone who finds an unattended logged-in session owns the
    account permanently rather than until the cookie expires.
    """

    current_password = serializers.CharField(write_only=True, style={"input_type": "password"})
    new_password = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate_current_password(self, value: str) -> str:
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate_new_password(self, value: str) -> str:
        _validate_password(value, self.context["request"].user)
        return value


class PasswordResetRequestSerializer(serializers.Serializer):
    """Just an email. Whether it exists is never revealed."""

    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    """
    The uid and token from the emailed link, plus the new password.

    Validation of the token pair happens in the view, not here: it needs the user
    row to run the password validators against, and a serializer that resolved the
    user would tempt a caller into using it before the token was checked.
    """

    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, style={"input_type": "password"})
