import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class ProjectQuerySet(models.QuerySet):
    def visible_to(self, user):
        if not user.is_authenticated or not user.is_active:
            return self.none()
        if user.is_superuser:
            return self
        return self.filter(memberships__user=user)


class Project(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    website = models.URLField(max_length=2048)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name="created_projects")
    objects = ProjectQuerySet.as_manager()

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Projectbeheerder"
        EDITOR = "editor", "Bewerker"
        VIEWER = "viewer", "Lezer"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="project_memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.VIEWER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "user"], name="unique_project_user")]

    def clean(self):
        if self.user_id and not self.user.is_active:
            raise ValidationError({"user": "Kies een actieve gebruiker."})
