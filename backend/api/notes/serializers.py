from rest_framework import serializers

from .models import Note


class NoteSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(
        source="created_by.full_name", read_only=True, default=""
    )
    created_by_initials = serializers.SerializerMethodField()

    class Meta:
        model = Note
        fields = [
            "id", "project", "kind", "who", "org", "body", "ref",
            "created_by", "created_by_name", "created_by_initials",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "created_by", "created_by_name", "created_by_initials",
            "created_at", "updated_at",
        ]

    def get_created_by_initials(self, obj) -> str:
        """`RG` for the avatar chip beside each note."""
        user = obj.created_by
        if user is None:
            return "—"
        parts = (user.full_name or "").split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        if parts:
            return parts[0][:2].upper()
        return (user.email or "?")[:2].upper()
