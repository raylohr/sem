import uuid
from django.db import models
from projects.models import Project


class ProjectURL(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="urls")
    normalized_url = models.TextField()
    url_hash = models.CharField(max_length=64)
    host = models.CharField(max_length=253, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "url_hash"], name="unique_project_url_hash")]
        ordering = ["normalized_url", "id"]


class URLObservation(models.Model):
    class Scope(models.TextChoices):
        PAGE = "page", "URL-metingen"
        SUBTREE = "subtree", "Sitestructuur / paden"
    url = models.ForeignKey(ProjectURL, on_delete=models.CASCADE, related_name="observations")
    batch = models.ForeignKey("sources.ImportBatch", on_delete=models.CASCADE, related_name="url_observations")
    source_record = models.OneToOneField("sources.SourceRecord", on_delete=models.CASCADE, related_name="url_observation")
    original_url = models.TextField()
    scope = models.CharField(max_length=10, choices=Scope.choices, default=Scope.PAGE)
    measured_date = models.DateField(null=True, blank=True)
    measured_at = models.DateTimeField(null=True, blank=True)
    facts = models.JSONField(default=dict)
    adapter_version = models.CharField(max_length=32, default="url-v1")

    class Meta:
        indexes = [models.Index(fields=["batch", "scope", "measured_date"], name="url_obs_batch_scope_date")]
