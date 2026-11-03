"""Versioned technology knowledge, scoped observations and customer declarations."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class Supplier(models.Model):
    key = models.SlugField(unique=True)
    name = models.CharField(max_length=200)
    country = models.CharField(max_length=2, blank=True)
    parent_name = models.CharField(max_length=200, blank=True)
    parent_country = models.CharField(max_length=2, blank=True)
    sources = models.JSONField(default=list)
    checked_at = models.DateField(null=True)
    revision = models.PositiveIntegerField(default=1)
    notes = models.TextField(blank=True)
    def __str__(self):
        return self.name


class SupplierRevision(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='revisions')
    revision = models.PositiveIntegerField()
    snapshot = models.JSONField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['supplier', 'revision'], name='supplier_revision_identity')]


class Technology(models.Model):
    key = models.SlugField(unique=True)
    name = models.CharField(max_length=150)
    category = models.CharField(max_length=40)
    description = models.TextField(blank=True)
    website = models.URLField(blank=True)
    aliases = models.JSONField(default=list)
    def __str__(self):
        return self.name


class TechnologySupplier(models.Model):
    technology = models.ForeignKey(Technology, on_delete=models.CASCADE, related_name='supplier_relations')
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT)
    role = models.CharField(max_length=30, default='publisher')
    source_url = models.URLField(blank=True)
    valid_from = models.DateField(default=timezone.localdate)
    valid_until = models.DateField(null=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['technology', 'supplier', 'role', 'valid_from'], name='technology_supplier_identity')]


class DetectionRule(models.Model):
    key = models.CharField(max_length=100)
    version = models.CharField(max_length=30)
    technology = models.ForeignKey(Technology, on_delete=models.CASCADE)
    kind = models.CharField(max_length=30)
    pattern = models.CharField(max_length=300)
    config = models.JSONField(default=dict)
    source_url = models.URLField(blank=True)
    active = models.BooleanField(default=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['key', 'version'], name='detection_rule_version')]


class EnrichmentRun(models.Model):
    batch = models.ForeignKey('sources.ImportBatch', on_delete=models.CASCADE, related_name='intelligence_runs')
    version = models.CharField(max_length=30, default='intel-v1')
    status = models.CharField(max_length=16, default='pending')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    started_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)
    coverage = models.JSONField(default=dict)
    european_profile = models.JSONField(default=dict)
    error = models.CharField(max_length=255, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['batch', 'version'], name='enrichment_batch_version')]


class DomainObservation(models.Model):
    run = models.ForeignKey(EnrichmentRun, on_delete=models.CASCADE, related_name='domains')
    hostname = models.CharField(max_length=253)
    measured_at = models.DateTimeField(default=timezone.now)
    records = models.JSONField(default=dict)
    network = models.JSONField(default=dict)
    coverage = models.JSONField(default=dict)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['run', 'hostname'], name='domain_observation_identity')]


class TechnologyDetection(models.Model):
    run = models.ForeignKey(EnrichmentRun, on_delete=models.CASCADE, related_name='detections')
    technology = models.ForeignKey(Technology, on_delete=models.PROTECT)
    visit = models.ForeignKey('browser_scans.BrowserVisit', null=True, on_delete=models.CASCADE)
    rule = models.ForeignKey(DetectionRule, on_delete=models.PROTECT)
    confidence = models.CharField(max_length=24, default='observed')
    state = models.CharField(max_length=30)
    version = models.CharField(max_length=60, blank=True)
    phase = models.CharField(max_length=30, blank=True)
    fingerprint = models.CharField(max_length=64)
    measured_at = models.DateTimeField(default=timezone.now)
    supplier_snapshot = models.JSONField(default=list)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['run', 'fingerprint'], name='technology_detection_identity')]
        indexes = [models.Index(fields=['run', 'technology', 'phase'], name='technology_detection_lookup')]


class DetectionEvidence(models.Model):
    detection = models.ForeignKey(TechnologyDetection, on_delete=models.CASCADE, related_name='evidence')
    source_record = models.ForeignKey('sources.SourceRecord', null=True, on_delete=models.SET_NULL)
    artifact = models.ForeignKey('browser_scans.BrowserArtifact', null=True, on_delete=models.SET_NULL)
    resource = models.ForeignKey('browser_scans.BrowserResource', null=True, on_delete=models.SET_NULL)
    domain = models.ForeignKey(DomainObservation, null=True, on_delete=models.SET_NULL)
    snapshot = models.JSONField(default=dict)


class CustomerDeclaration(models.Model):
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE, related_name='technology_declarations')
    technology = models.ForeignKey(Technology, on_delete=models.PROTECT)
    kind = models.CharField(max_length=30, default='usage')
    statement = models.TextField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class PolicyDocument(models.Model):
    run = models.ForeignKey(EnrichmentRun, on_delete=models.CASCADE, related_name='policies')
    url = models.TextField()
    measured_at = models.DateTimeField(default=timezone.now)
    status = models.PositiveSmallIntegerField()
    text = models.TextField()
    body = models.BinaryField()
    checksum = models.CharField(max_length=64)
    truncated = models.BooleanField(default=False)
