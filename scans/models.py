import uuid
from django.conf import settings
from django.db import models
from django.db.models.functions import MD5
from projects.models import Project


class ScanTarget(models.Model):
    class Kind(models.TextChoices):
        OWN = "own", "Eigen website"
        COMPETITOR = "competitor", "Concurrent"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="scan_targets")
    name = models.CharField(max_length=200)
    url = models.URLField(max_length=2048)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.OWN)
    allowed_hosts = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class ScanDocument(models.Model):
    batch = models.ForeignKey("sources.ImportBatch", on_delete=models.CASCADE, related_name="scan_documents")
    index = models.PositiveIntegerField()
    url = models.URLField(max_length=4096)
    status = models.PositiveIntegerField()
    content_type = models.CharField(max_length=255)
    fetched_at = models.DateTimeField()
    body = models.BinaryField()
    class Meta:
        indexes = [models.Index('batch', MD5('url'), name='scan_document_url_digest')]
        constraints = [models.UniqueConstraint(fields=["batch", "index"], name="unique_batch_scan_document")]


class HostPolicy(models.Model):
    NAV_CHOICES = [('pending','Te beoordelen'),('allowed','Toestaan'),('blocked','Uitsluiten')]
    RESOURCE_CHOICES = [('pending','Standaard'),('allowed','Toestaan'),('blocked','Uitsluiten')]
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='host_policies')
    host = models.CharField(max_length=253)
    navigation = models.CharField(max_length=10, default='pending', choices=[('pending','Te beoordelen'),('allowed','Toestaan'),('blocked','Uitsluiten')])
    resources = models.CharField(max_length=10, default='pending', choices=[('pending','Standaard'),('allowed','Toestaan'),('blocked','Uitsluiten')])
    external=models.CharField(max_length=10,default='pending',choices=NAV_CHOICES)
    discovered_batch = models.ForeignKey('sources.ImportBatch', null=True, on_delete=models.SET_NULL)
    example_url = models.CharField(max_length=4096, blank=True)
    source_url = models.CharField(max_length=4096, blank=True)
    purpose = models.CharField(max_length=20, default='navigation')
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['project','host'],name='unique_project_host_policy')]


class CrawlAttempt(models.Model):
    """One logical page fetch (including HTTP redirects), never a fabricated historical attempt."""
    id=models.UUIDField(primary_key=True)
    batch=models.ForeignKey('sources.ImportBatch',on_delete=models.CASCADE,related_name='crawl_attempts')
    url=models.TextField()
    url_hash=models.CharField(max_length=64)
    ordinal=models.PositiveSmallIntegerField()
    state=models.CharField(max_length=20)
    started_at=models.DateTimeField()
    finished_at=models.DateTimeField(null=True)
    http_status=models.PositiveSmallIntegerField(null=True)
    error_code=models.CharField(max_length=50,blank=True)
    details=models.JSONField(default=dict)
    @property
    def error_label(self):
        from .diagnostics import MESSAGES
        return MESSAGES.get(self.error_code,self.error_code or 'Eindantwoord onbekend')
    class Meta:
        constraints=[models.UniqueConstraint(fields=['batch','url_hash','ordinal'],name='unique_crawl_attempt_number')]
        indexes=[models.Index(fields=['batch','url_hash','ordinal'],name='crawl_attempt_lookup')]


class ScanRetryTarget(models.Model):
    batch=models.ForeignKey('sources.ImportBatch',on_delete=models.CASCADE,related_name='retry_targets')
    source_record=models.ForeignKey('sources.SourceRecord',null=True,on_delete=models.SET_NULL)
    url=models.TextField()
    url_hash=models.CharField(max_length=64)
    snapshot=models.JSONField(default=dict)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['batch','url_hash'],name='unique_retry_target')]


class CrawlURLCoverage(models.Model):
    batch=models.ForeignKey('sources.ImportBatch',on_delete=models.CASCADE,related_name='crawl_url_coverage')
    url=models.TextField()
    url_hash=models.CharField(max_length=64)
    in_sitemap=models.BooleanField(null=True)
    in_links=models.BooleanField(null=True)
    is_start=models.BooleanField(default=False)
    sitemap_url=models.TextField(blank=True)
    outcome=models.CharField(max_length=20)
    queued=models.BooleanField(default=False)
    attempted=models.BooleanField(default=False)
    http_status=models.PositiveSmallIntegerField(null=True)
    error_code=models.CharField(max_length=50,blank=True)
    depth=models.PositiveIntegerField(null=True)
    @property
    def error_label(self):
        from .diagnostics import MESSAGES
        return MESSAGES.get(self.error_code,self.error_code)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['batch','url_hash'],name='unique_crawl_coverage_url')]
        indexes=[models.Index(fields=['batch','outcome'],name='crawl_coverage_status'),
                 models.Index(fields=['batch','in_sitemap','in_links'],name='crawl_coverage_source')]


class ResourceMeasurement(models.Model):
    """One target per batch, purpose and baseline request profile; not a content page."""
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    batch=models.ForeignKey('sources.ImportBatch',on_delete=models.CASCADE,related_name='resource_measurements')
    purpose=models.CharField(max_length=12)
    url=models.CharField(max_length=4096)
    url_hash=models.CharField(max_length=64)
    outcome=models.CharField(max_length=32)
    http_status=models.PositiveSmallIntegerField(null=True)
    final_url=models.CharField(max_length=4096,blank=True)
    measured_at=models.DateTimeField(null=True)
    headers=models.JSONField(default=dict)
    measurements=models.JSONField(default=dict)
    evidence=models.JSONField(default=dict)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['batch','purpose','url_hash'],name='resource_batch_purpose_url')]
        indexes=[models.Index(fields=['batch','purpose','outcome'],name='resource_batch_outcome')]


class ResourceReference(models.Model):
    target=models.ForeignKey(ResourceMeasurement,on_delete=models.CASCADE,related_name='references')
    reference_hash=models.CharField(max_length=64)
    source_url=models.CharField(max_length=4096)
    locator=models.CharField(max_length=120,blank=True)
    element_path=models.CharField(max_length=1024,blank=True)
    kind=models.CharField(max_length=32)
    parent_url=models.CharField(max_length=4096,blank=True)
    details=models.JSONField(default=dict)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['target','reference_hash'],name='resource_reference_identity')]
        indexes=[models.Index('target',MD5('source_url'),name='resource_ref_source')]
