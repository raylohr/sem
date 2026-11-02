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


class Label(models.Model):
    class Kind(models.TextChoices):
        EXPERTISE = 'expertise', 'Expertise'
        SOURCE = 'source', 'Bronlabel (nog te beoordelen)'
        FUNCTIONAL = 'functional', 'Functioneel label'
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='labels')
    name = models.TextField()
    key = models.CharField(max_length=64)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.SOURCE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name', 'id']
        constraints = [models.UniqueConstraint(fields=['project', 'key'], name='unique_project_label_key')]


class URLLabel(models.Model):
    url = models.ForeignKey(ProjectURL, on_delete=models.CASCADE, related_name='label_assignments')
    label = models.ForeignKey(Label, on_delete=models.CASCADE, related_name='assignments')
    status = models.CharField(max_length=16, choices=[('source', 'Volgens bron'), ('reviewed', 'Beoordeeld')], default='source')
    role = models.CharField(max_length=16, choices=[('', 'Niet bepaald'), ('primary', 'Primair'), ('additional', 'Aanvullend')], blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['url', 'label'], name='unique_url_label')]


class LabelEvidence(models.Model):
    assignment = models.ForeignKey(URLLabel, on_delete=models.CASCADE, related_name='evidence')
    source_record = models.ForeignKey('sources.SourceRecord', on_delete=models.CASCADE, related_name='label_evidence')
    column = models.CharField(max_length=200)
    original_label = models.TextField()
    original_url = models.TextField()
    imported_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['assignment', 'source_record', 'column'], name='unique_label_evidence')]
