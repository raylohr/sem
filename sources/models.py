import uuid
from django.conf import settings
from django.db import models
from projects.models import Project


class ImportBatch(models.Model):
    class Source(models.TextChoices):
        GSC = "gsc", "Google Search Console"
        AHREFS = "ahrefs", "Ahrefs"
        SCREAMING_FROG = "screaming_frog", "Screaming Frog"
    class Method(models.TextChoices):
        UPLOAD = "upload", "Upload"
        API = "api", "API"
    class Status(models.TextChoices):
        PENDING = "pending", "Wacht op verwerking"
        PROCESSING = "processing", "Wordt verwerkt"
        COMPLETE = "complete", "Voltooid"
        INCOMPLETE = "incomplete", "Onvolledig"
        FAILED = "failed", "Mislukt"
        CANCELLED = "cancelled", "Geannuleerd"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="import_batches")
    source = models.CharField(max_length=24, choices=Source.choices)
    method = models.CharField(max_length=10, choices=Method.choices)
    label = models.CharField(max_length=255)
    original_filename = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    imported_at = models.DateTimeField(auto_now_add=True)
    measured_at = models.DateTimeField(null=True, blank=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    record_count = models.PositiveIntegerField(default=0)
    parser_version = models.CharField(max_length=64, blank=True)
    checksum = models.CharField(max_length=64, blank=True)
    warnings = models.JSONField(default=list, blank=True)

    file = models.FileField(upload_to="imports/%Y/%m/", blank=True, max_length=300)
    fingerprint = models.CharField(max_length=64, blank=True, db_index=True)
    sheet_summary = models.JSONField(default=list, blank=True)
    error = models.CharField(max_length=255, blank=True)
    gsc_connection = models.ForeignKey("integrations.GSCConnection", null=True, blank=True, on_delete=models.PROTECT)
    request_metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-imported_at", "-id"]
        constraints = [models.CheckConstraint(condition=(models.Q(period_start__isnull=True) | models.Q(period_end__isnull=True) | models.Q(period_end__gte=models.F("period_start"))), name="valid_import_period")]


class SourceRecord(models.Model):
    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="records")
    locator = models.CharField(max_length=255)
    data = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["batch", "locator"], name="unique_batch_locator")]


class BatchDeletionEvent(models.Model):
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL)
    project_identifier = models.UUIDField(null=True, blank=True, editable=False)
    batch_id = models.UUIDField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    deleted_at = models.DateTimeField(auto_now_add=True)
    # Deliberately no filenames, payloads or source content in deletion history.


class PendingFileDeletion(models.Model):
    path = models.CharField(max_length=300, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
