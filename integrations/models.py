import uuid
from decimal import Decimal
from django.core.validators import MinValueValidator, MaxValueValidator
from .playwright_config import PROFILE_CHOICES
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


class PlaywrightCrawlSettings(models.Model):
    """Step 1: saved configuration only; execution is introduced separately."""
    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='playwright_settings')
    respect_robots = models.BooleanField(default=False)
    use_sitemaps = models.BooleanField(default=True)
    discover_urls = models.BooleanField(default=True)
    start_urls = models.JSONField(default=list, blank=True)
    sitemap_urls = models.JSONField(default=list, blank=True)
    excluded_hosts = models.JSONField(default=list, blank=True)
    excluded_paths = models.JSONField(default=list, blank=True)
    desktop_profile = models.CharField(max_length=32, choices=PROFILE_CHOICES, default='random')
    pages_per_second = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('1.00'),
        validators=[MinValueValidator(Decimal('0.05')), MaxValueValidator(Decimal('20.00'))])
    parallel_agents = models.PositiveSmallIntegerField(default=1,
        validators=[MinValueValidator(1), MaxValueValidator(20)])
    slow_after_seconds = models.PositiveSmallIntegerField(default=10,
        validators=[MinValueValidator(1), MaxValueValidator(120)])
    slow_pages_per_second = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('0.25'),
        validators=[MinValueValidator(Decimal('0.05')), MaxValueValidator(Decimal('20.00'))])
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(parallel_agents__gte=1, parallel_agents__lte=20), name='playwright_agents_1_to_20'),
            models.CheckConstraint(condition=models.Q(pages_per_second__gte='0.05', pages_per_second__lte='20'), name='playwright_rate_bounds'),
            models.CheckConstraint(condition=models.Q(slow_pages_per_second__gte='0.05', slow_pages_per_second__lte=models.F('pages_per_second')), name='playwright_slow_rate_bounds'),
            models.CheckConstraint(condition=models.Q(slow_after_seconds__gte=1, slow_after_seconds__lte=120), name='playwright_slow_seconds_bounds'),
        ]

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError
        from .playwright_config import clean_urls, clean_excluded_hosts, clean_excluded_paths, is_excluded
        errors = {}
        for field, cleaner in (
            ('start_urls', lambda values: clean_urls(values, self.project)),
            ('sitemap_urls', lambda values: clean_urls(values, self.project, maximum=100)),
            ('excluded_hosts', lambda values: clean_excluded_hosts(values, self.project)),
            ('excluded_paths', clean_excluded_paths),
        ):
            try:
                setattr(self, field, cleaner(getattr(self, field)))
            except ValidationError as error:
                errors[field] = error.messages
        for field in ('start_urls', 'sitemap_urls'):
            if field not in errors and not {'excluded_hosts', 'excluded_paths'} & set(errors):
                if any(is_excluded(url, self.excluded_hosts, self.excluded_paths) for url in getattr(self, field)):
                    errors[field] = ['Deze lijst bevat een URL die door de uitsluitingen wordt geblokkeerd.']
        if errors:
            raise ValidationError(errors)
