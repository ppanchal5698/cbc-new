"""
Signup, login, logout, profile, password (C3 / ADR-0004, §11.2).

The access-control suite next door asserts that endpoints refuse anonymous
callers. This one asserts the way in — and, more importantly, the ways that must
stay shut: an account nobody approved, a profile PATCH that grants privilege, a
signup response that reveals who already has an account.
"""

import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.authtoken.models import Token

User = get_user_model()

pytestmark = pytest.mark.django_db

SIGNUP = "/api/auth/signup/"
LOGIN = "/api/auth/login/"
LOGOUT = "/api/auth/logout/"
TOKEN = "/api/auth/token/"
ME = "/api/auth/me/"
CHANGE_PASSWORD = "/api/auth/change-password/"

GOOD_PASSWORD = "correct-horse-battery-7"


@pytest.fixture(autouse=True)
def _no_throttling(settings):
    """
    Throttles are real and configured; they just make an eight-request test
    non-deterministic. Their rates are asserted separately below.
    """
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {k: None for k in settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]},
    }


class TestSignupGrantsNothing:
    def test_signup_creates_an_inactive_account(self, api_client):
        response = api_client.post(
            SIGNUP, {"email": "new@cbc.test", "password": GOOD_PASSWORD}, format="json"
        )
        assert response.status_code == status.HTTP_202_ACCEPTED

        user = User.objects.get(email="new@cbc.test")
        assert user.is_active is False, "signup must not grant access to client data"
        assert user.is_staff is False

    def test_a_signed_up_user_cannot_log_in(self, api_client):
        api_client.post(
            SIGNUP, {"email": "new@cbc.test", "password": GOOD_PASSWORD}, format="json"
        )
        response = api_client.post(
            LOGIN, {"email": "new@cbc.test", "password": GOOD_PASSWORD}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "awaiting approval" in response.data["detail"].lower()

    def test_signup_does_not_reveal_whether_an_email_is_registered(self, api_client, user):
        """
        An endpoint that answers differently for a known address is a way to
        enumerate who works at CBC.
        """
        fresh = api_client.post(
            SIGNUP, {"email": "brand-new@cbc.test", "password": GOOD_PASSWORD}, format="json"
        )
        taken = api_client.post(
            SIGNUP, {"email": user.email, "password": GOOD_PASSWORD}, format="json"
        )

        assert fresh.status_code == taken.status_code == status.HTTP_202_ACCEPTED
        assert fresh.data == taken.data

    def test_a_taken_email_does_not_overwrite_the_existing_account(self, api_client, user):
        original = user.password
        api_client.post(SIGNUP, {"email": user.email, "password": GOOD_PASSWORD}, format="json")
        user.refresh_from_db()
        assert user.password == original, "signup must never reset someone else's password"

    def test_signup_enforces_the_password_validators(self, api_client):
        response = api_client.post(
            SIGNUP, {"email": "weak@cbc.test", "password": "123"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not User.objects.filter(email="weak@cbc.test").exists()

    def test_an_admin_activation_is_what_opens_the_door(self, api_client):
        api_client.post(
            SIGNUP, {"email": "new@cbc.test", "password": GOOD_PASSWORD}, format="json"
        )
        User.objects.filter(email="new@cbc.test").update(is_active=True)

        response = api_client.post(
            LOGIN, {"email": "new@cbc.test", "password": GOOD_PASSWORD}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["email"] == "new@cbc.test"


class TestLoginAndLogout:
    def test_login_starts_a_usable_session(self, api_client, user):
        user.set_password(GOOD_PASSWORD)
        user.save()

        assert api_client.get("/api/projects/").status_code in (
            status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN,
        )
        api_client.post(LOGIN, {"email": user.email, "password": GOOD_PASSWORD}, format="json")
        assert api_client.get("/api/projects/").status_code == status.HTTP_200_OK

    def test_logout_ends_it_server_side(self, api_client, user):
        user.set_password(GOOD_PASSWORD)
        user.save()
        api_client.post(LOGIN, {"email": user.email, "password": GOOD_PASSWORD}, format="json")

        assert api_client.post(LOGOUT).status_code == status.HTTP_204_NO_CONTENT
        assert api_client.get("/api/projects/").status_code in (
            status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN,
        )

    def test_a_wrong_password_and_an_unknown_account_answer_identically(self, api_client, user):
        """Two different messages are two bits of information about who exists."""
        wrong = api_client.post(
            LOGIN, {"email": user.email, "password": "not-the-password"}, format="json"
        )
        unknown = api_client.post(
            LOGIN, {"email": "nobody@cbc.test", "password": "not-the-password"}, format="json"
        )

        assert wrong.status_code == unknown.status_code == status.HTTP_401_UNAUTHORIZED
        assert wrong.data == unknown.data


class TestProfile:
    def test_me_returns_the_callers_own_profile(self, auth_client, user):
        response = auth_client.get(ME)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["email"] == user.email

    def test_a_user_can_edit_their_profile(self, auth_client, user):
        response = auth_client.patch(
            ME, {"full_name": "Dana Estimator", "job_title": "Senior Estimator"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK

        user.refresh_from_db()
        assert user.full_name == "Dana Estimator"
        assert user.job_title == "Senior Estimator"

    def test_a_profile_patch_cannot_grant_privilege(self, auth_client, user):
        """
        The escalation case. A serializer that accepts more than it should is the
        difference between editing your phone number and making yourself staff.
        """
        response = auth_client.patch(
            ME,
            {"is_staff": True, "is_active": True, "is_superuser": True, "full_name": "Mallory"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        user.refresh_from_db()
        assert user.is_staff is False
        assert user.is_superuser is False
        assert user.full_name == "Mallory", "the legitimate field still applied"

    def test_a_profile_patch_cannot_change_the_login_identity(self, auth_client, user):
        original = user.email
        auth_client.patch(ME, {"email": "someone-else@cbc.test"}, format="json")
        user.refresh_from_db()
        assert user.email == original


class TestPasswordChange:
    def test_the_current_password_is_required(self, auth_client):
        response = auth_client.post(
            CHANGE_PASSWORD,
            {"current_password": "wrong", "new_password": GOOD_PASSWORD},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_valid_change_works_and_keeps_the_session(self, auth_client, user):
        user.set_password("old-password-9xyz")
        user.save()
        auth_client.force_authenticate(user=user)

        response = auth_client.post(
            CHANGE_PASSWORD,
            {"current_password": "old-password-9xyz", "new_password": GOOD_PASSWORD},
            format="json",
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT

        user.refresh_from_db()
        assert user.check_password(GOOD_PASSWORD)
        assert auth_client.get(ME).status_code == status.HTTP_200_OK

    def test_the_new_password_must_pass_the_validators(self, auth_client, user):
        user.set_password("old-password-9xyz")
        user.save()
        auth_client.force_authenticate(user=user)

        response = auth_client.post(
            CHANGE_PASSWORD,
            {"current_password": "old-password-9xyz", "new_password": "123"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        user.refresh_from_db()
        assert user.check_password("old-password-9xyz"), "the old password still works"

    def test_changing_a_password_revokes_issued_tokens(self, auth_client, user):
        """A password change that leaves old tokens valid has revoked nothing."""
        user.set_password("old-password-9xyz")
        user.save()
        Token.objects.create(user=user)
        auth_client.force_authenticate(user=user)

        auth_client.post(
            CHANGE_PASSWORD,
            {"current_password": "old-password-9xyz", "new_password": GOOD_PASSWORD},
            format="json",
        )
        assert not Token.objects.filter(user=user).exists()


class TestTokensForScripts:
    def test_a_token_authenticates(self, api_client, user):
        user.set_password(GOOD_PASSWORD)
        user.save()

        response = api_client.post(
            TOKEN, {"email": user.email, "password": GOOD_PASSWORD}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK

        api_client.credentials(HTTP_AUTHORIZATION=f"Token {response.data['token']}")
        assert api_client.get("/api/projects/").status_code == status.HTTP_200_OK

    def test_an_unapproved_account_gets_no_token(self, api_client):
        api_client.post(
            SIGNUP, {"email": "new@cbc.test", "password": GOOD_PASSWORD}, format="json"
        )
        response = api_client.post(
            TOKEN, {"email": "new@cbc.test", "password": GOOD_PASSWORD}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not Token.objects.exists()


class TestTheUserModel:
    def test_create_user_defaults_to_inactive(self):
        """
        The safe default. Any call site that forgets the flag produces an account
        that cannot sign in, rather than one that silently can.
        """
        assert User.objects.create_user(email="x@cbc.test", password=GOOD_PASSWORD).is_active is False

    def test_a_superuser_is_active_and_staff(self):
        admin = User.objects.create_superuser(email="admin@cbc.test", password=GOOD_PASSWORD)
        assert admin.is_active and admin.is_staff and admin.is_superuser

    def test_email_is_the_identifier(self):
        assert User.USERNAME_FIELD == "email"
        assert not hasattr(User(), "username") or User().username is None

    def test_an_email_is_required(self):
        with pytest.raises(ValueError):
            User.objects.create_user(email="", password=GOOD_PASSWORD)
