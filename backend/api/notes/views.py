from rest_framework import viewsets

from .models import Note
from .serializers import NoteSerializer


class NoteViewSet(viewsets.ModelViewSet):
    """
    Calls and notes against a bid.

    Newest first: the last thing said is the thing an estimator is looking for.
    """

    queryset = Note.objects.select_related("created_by", "project").all()
    serializer_class = NoteSerializer
    filterset_fields = ["project", "kind"]
    search_fields = ["body", "who", "org", "ref"]
    ordering_fields = ["created_at"]

    def perform_create(self, serializer):
        serializer.save(
            created_by=self.request.user if self.request.user.is_authenticated else None
        )
