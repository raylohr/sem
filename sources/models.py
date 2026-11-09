import uuid
from django.conf import settings
from django.db import models
from projects.models import Project


class AtomicInventorySource(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        from django.db import transaction
        with transaction.atomic(using=kwargs.get('using') or self._state.db):
            return super().save(*args, **kwargs)


# Snapshot statuses whose records count in analyses. An unclassified (NULL)
# import also counts, so data stays visible until classify_snapshots --apply.
COUNTED_SNAPSHOTS = ('active', 'supplement', 'independent')


class ImportBatchQuerySet(models.QuerySet):
    def published(self):
        return self.filter(status__in=('complete', 'incomplete'))

    def counted(self):
        """The single rule for which imports feed analyses and exports."""
        return self.published().filter(models.Q(snapshot_status__isnull=True) | models.Q(snapshot_status__in=COUNTED_SNAPSHOTS))

    def analysable(self):
        """Counted imports with per-URL records (summary and external reports stay out of the inventory)."""
        # Explicit has_key branch: a NOT IN on a missing JSON key is NULL in SQL.
        return self.counted().filter(~models.Q(request_metadata__has_key='record_scope')
            | ~models.Q(request_metadata__record_scope__in=('summary', 'external')))


class ImportBatch(AtomicInventorySource):
    objects = ImportBatchQuerySet.as_manager()

    class Snapshot(models.TextChoices):
        ACTIVE = "active", "Actief"
        SUPPLEMENT = "supplement", "Aanvullend"
        SUPERSEDED = "superseded", "Vervangen"
        REDUNDANT = "redundant", "Redundant"
        HISTORICAL = "historical", "Historisch"
        CONFLICT = "conflict", "Conflict"
        INDEPENDENT = "independent", "Zelfstandig"

    class Source(models.TextChoices):
        GSC = "gsc", "Google Search Console"
        SF = "sf", "Screaming Frog"
        AHREFS = "ahrefs", "Ahrefs"
        SERANKING = "seranking", "SE Ranking"
    class Method(models.TextChoices):
        API = "api", "API"
        FILE = "file", "Bestand"
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
    file_checksum = models.CharField(max_length=64, null=True, blank=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    imported_at = models.DateTimeField(auto_now_add=True)
    measured_at = models.DateTimeField(null=True, blank=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    record_count = models.PositiveIntegerField(default=0)
    parser_version = models.CharField(max_length=64, blank=True)
    warnings = models.JSONField(default=list, blank=True)

    error = models.CharField(max_length=255, blank=True)
    gsc_connection = models.ForeignKey("integrations.GSCConnection", null=True, blank=True, on_delete=models.PROTECT)
    seranking_connection = models.ForeignKey("integrations.SERankingConnection", null=True, blank=True, on_delete=models.PROTECT)
    request_metadata = models.JSONField(default=dict, blank=True)
    # Dataset = project + source + report type (+ segment, e.g. a GSC indexing category).
    dataset_key = models.CharField(max_length=255, blank=True, default='')
    snapshot_status = models.CharField(max_length=12, choices=Snapshot.choices, null=True, blank=True)
    snapshot_reason = models.TextField(blank=True)
    quality_warnings = models.JSONField(default=list, blank=True)
    superseded_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    @property
    def counts_in_analyses(self):
        return self.status in ('complete', 'incomplete') and (self.snapshot_status is None or self.snapshot_status in COUNTED_SNAPSHOTS)

    class Meta:
        ordering = ["-imported_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["project", "dataset_key"], condition=models.Q(snapshot_status="active"), name="one_active_snapshot_per_dataset"), models.UniqueConstraint(fields=["project", "file_checksum"], name="unique_project_file_import"), models.CheckConstraint(condition=(models.Q(period_start__isnull=True) | models.Q(period_end__isnull=True) | models.Q(period_end__gte=models.F("period_start"))), name="valid_import_period")]


class SourceRecordQuerySet(models.QuerySet):
    def delete(self):
        from django.db import transaction
        from .inventory_index import invalidate
        with transaction.atomic(using=self.db):
            projects = list(self.filter(batch__status__in=('complete', 'incomplete')).values_list('batch__project_id', flat=True).distinct())
            result = super().delete()
            for project_id in projects: invalidate(project_id)
            return result

    def update(self, **kwargs):
        from django.db import transaction
        from .inventory_index import invalidate
        with transaction.atomic(using=self.db):
            projects = list(self.filter(batch__status__in=('complete', 'incomplete')).values_list('batch__project_id', flat=True).distinct())
            count = super().update(**kwargs)
            for project_id in projects: invalidate(project_id)
            return count

    def bulk_create(self, objs, **kwargs):
        from django.db import transaction
        from .inventory_index import invalidate
        objs = list(objs)
        with transaction.atomic(using=self.db):
            result = super().bulk_create(objs, **kwargs)
            for project_id in ImportBatch.objects.filter(pk__in={obj.batch_id for obj in objs}, status__in=('complete', 'incomplete')).values_list('project_id', flat=True).distinct():
                invalidate(project_id)
            return result


class SourceRecord(AtomicInventorySource):
    objects = SourceRecordQuerySet.as_manager()
    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="records")
    locator = models.CharField(max_length=255)
    data = models.JSONField()

    def delete(self, *args, **kwargs):
        from django.db import transaction
        from .inventory_index import invalidate
        with transaction.atomic():
            project_id = self.batch.project_id
            result = super().delete(*args, **kwargs)
            if self.batch.status in ('complete', 'incomplete'): invalidate(project_id)
            return result

    class Meta:
        constraints = [models.UniqueConstraint(fields=["batch", "locator"], name="unique_batch_locator")]


class BatchDeletionEvent(models.Model):
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL)
    project_identifier = models.UUIDField(null=True, blank=True, editable=False)
    batch_id = models.UUIDField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    deleted_at = models.DateTimeField(auto_now_add=True)
    # Deliberately no filenames, payloads or source content in deletion history.


class InventoryPreference(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    columns = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'project'], name='unique_user_project_inventory')]


class ProjectViewPreference(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    inventory_query = models.TextField(blank=True)
    performance = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'project'], name='unique_user_project_view')]


class InventoryState(models.Model):
    project = models.OneToOneField(Project, on_delete=models.CASCADE, primary_key=True)
    revision = models.PositiveBigIntegerField(default=0)
    built_revision = models.BigIntegerField(default=-1)
    url_count = models.PositiveBigIntegerField(default=0)
    metrics = models.JSONField(default=dict)
    generation = models.UUIDField(null=True)
    updated_at = models.DateTimeField(auto_now=True)


class InventoryField(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    generation = models.UUIDField()
    key = models.CharField(max_length=64)
    definition = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['project', 'generation', 'key'], name='inventory_field_identity')]


class InventoryURL(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    generation = models.UUIDField()
    url = models.TextField()
    path = models.TextField()
    url_folded = models.TextField()
    payload = models.JSONField()
    top3 = models.BooleanField(null=True)
    top10 = models.BooleanField(null=True)
    not_found = models.BooleanField(null=True)

    class Meta:
        indexes = [models.Index(fields=['project', 'generation'], name='inventory_project_version')]


class InventoryValue(models.Model):
    url = models.ForeignKey(InventoryURL, on_delete=models.CASCADE, related_name='values')
    field_key = models.CharField(max_length=64)
    observation = models.JSONField()
    text = models.TextField(null=True)
    folded = models.TextField(null=True)
    number = models.DecimalField(max_digits=1000, decimal_places=512, null=True)
    date = models.DateField(null=True)
    choice_key = models.CharField(max_length=64, default='__missing__')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['url', 'field_key'], name='inventory_url_field')]
        indexes = [
            models.Index(fields=['field_key', 'number'], name='inventory_numeric'),
            models.Index(fields=['field_key', 'date'], name='inventory_date'),
            models.Index(fields=['field_key', 'choice_key'], name='inventory_choice'),
        ]


class InventoryExport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token = models.UUIDField()
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    query = models.TextField(blank=True)
    state = models.CharField(max_length=12, default='queued')
    file = models.FileField(upload_to='private-exports', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['project', 'user', 'token'], name='inventory_export_token')]
