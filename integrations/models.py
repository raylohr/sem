import uuid
from django.conf import settings
from django.db import models
from projects.models import Project


class GSCConnection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="gsc_connections")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    property_url = models.CharField(max_length=2048)
    encrypted_refresh_token = models.TextField()
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    available_properties = models.JSONField(default=list, blank=True)
    daily_enabled = models.BooleanField(default=False)
    last_scheduled_date = models.DateField(null=True, blank=True)



class APIResponsePage(models.Model):
    batch = models.ForeignKey("sources.ImportBatch", on_delete=models.CASCADE, related_name="api_pages")
    page_index = models.PositiveIntegerField()
    requested_at = models.DateTimeField()
    request_body = models.JSONField()
    payload = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["batch", "page_index"], name="unique_batch_api_page")]


class GSCOAuthSettings(models.Model):
    """One platform-wide OAuth client; the client secret is always encrypted."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    client_id = models.CharField(max_length=255)
    encrypted_client_secret = models.TextField()
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name="single_gsc_oauth_settings")]


class SERankingConnection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="seranking_connections")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    site_id = models.PositiveBigIntegerField()
    site_name = models.CharField(max_length=2048)
    active = models.BooleanField(default=True)
    daily_enabled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['project', 'site_id'], name='unique_project_seranking_site')]


class SERankingRefresh(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Gepland'
        PROCESSING = 'processing', 'Wordt gecontroleerd'
        COMPLETE = 'complete', 'Bijgewerkt'
        PARTIAL = 'partial', 'Gedeeltelijk bijgewerkt'
        FAILED = 'failed', 'Mislukt'
        CANCELLED = 'cancelled', 'Uitgeschakeld'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(SERankingConnection, on_delete=models.CASCADE, related_name='refreshes')
    check_date = models.DateField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    run_token = models.UUIDField(null=True, blank=True)
    results = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-check_date', '-created_at']
        constraints = [models.UniqueConstraint(fields=['connection', 'check_date'], name='unique_seranking_daily_refresh')]
