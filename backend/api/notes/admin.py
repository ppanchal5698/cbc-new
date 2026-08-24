from django.contrib import admin

from .models import Note


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ("kind", "who", "org", "ref", "created_by", "created_at")
    list_filter = ("kind",)
    search_fields = ("body", "who", "org", "ref")
