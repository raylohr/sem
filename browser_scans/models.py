import uuid
from django.db import models


class BrowserRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.OneToOneField('sources.ImportBatch', on_delete=models.CASCADE, related_name='browser_run')
    source_batch = models.ForeignKey('sources.ImportBatch', null=True, on_delete=models.SET_NULL, related_name='browser_followups')
    config = models.JSONField(default=dict)
    coverage = models.JSONField(default=dict)
    started_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)
    engine = models.JSONField(default=dict)


class BrowserVisit(models.Model):
    run = models.ForeignKey(BrowserRun, on_delete=models.CASCADE, related_name='visits')
    url = models.TextField()
    url_hash = models.CharField(max_length=64)
    depth = models.PositiveIntegerField(default=0)
    discovery = models.CharField(max_length=24, default='sample')
    scenario = models.CharField(max_length=24, default='baseline', db_default='baseline')
    privacy = models.JSONField(default=dict, db_default={})
    status = models.CharField(max_length=16, default='pending')
    final_url = models.TextField(blank=True)
    http_status = models.PositiveSmallIntegerField(null=True)
    measured_at = models.DateTimeField(null=True)
    source_record = models.OneToOneField('sources.SourceRecord', null=True, on_delete=models.SET_NULL)
    server_facts = models.JSONField(default=dict)
    rendered_facts = models.JSONField(default=dict)
    differences = models.JSONField(default=dict)
    coverage = models.JSONField(default=dict)
    metrics = models.JSONField(default=dict)
    errors = models.JSONField(default=list)
    duration_ms = models.PositiveIntegerField(null=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['run', 'url_hash', 'scenario'], name='browser_visit_scenario')]
        indexes = [models.Index(fields=['run', 'status', 'id'], name='browser_visit_queue')]


class BrowserResource(models.Model):
    visit = models.ForeignKey(BrowserVisit, on_delete=models.CASCADE, related_name='resources')
    position = models.PositiveIntegerField()
    url = models.TextField()
    resource_type = models.CharField(max_length=40)
    status = models.PositiveSmallIntegerField(null=True)
    blocked_reason = models.CharField(max_length=80, blank=True)
    bytes = models.PositiveIntegerField(default=0)
    fetch_ms = models.FloatField(null=True)
    details = models.JSONField(default=dict)
    class Meta:
        indexes = [models.Index(fields=['visit', 'resource_type', 'status'], name='browser_resource_status')]


class AccessibilityIssue(models.Model):
    visit = models.ForeignKey(BrowserVisit, on_delete=models.CASCADE, related_name='accessibility_issues')
    rule = models.CharField(max_length=100)
    impact = models.CharField(max_length=24, blank=True)
    title = models.TextField()
    details = models.JSONField(default=dict)
    class Meta:
        indexes = [models.Index(fields=['visit', 'impact'], name='browser_accessibility_impact')]


class BrowserArtifact(models.Model):
    visit = models.ForeignKey(BrowserVisit, on_delete=models.CASCADE, related_name='artifacts')
    kind = models.CharField(max_length=24)
    content_type = models.CharField(max_length=80)
    checksum = models.CharField(max_length=64)
    size = models.PositiveIntegerField()
    body = models.BinaryField()
    class Meta:
        constraints = [models.UniqueConstraint(fields=['visit', 'kind'], name='browser_artifact_kind')]


class StorageObservation(models.Model):
    visit = models.ForeignKey(BrowserVisit, on_delete=models.CASCADE, related_name='storage_observations')
    phase = models.CharField(max_length=30)
    kind = models.CharField(max_length=20)
    name = models.CharField(max_length=256)
    origin = models.CharField(max_length=4096)
    metadata = models.JSONField(default=dict)
    class Meta:
        indexes = [models.Index(fields=['visit', 'phase', 'kind'], name='browser_storage_phase')]


class DiscoveredLink(models.Model):
    visit=models.ForeignKey(BrowserVisit,on_delete=models.CASCADE,related_name='discovered_links')
    url=models.CharField(max_length=4096)
    url_hash=models.CharField(max_length=64)
    phase=models.CharField(max_length=32)
    state=models.CharField(max_length=32)
    details=models.JSONField(default=dict)
    followup=models.ForeignKey('sources.ImportBatch',null=True,on_delete=models.SET_NULL,related_name='browser_discovered_links')
    class Meta:
        constraints=[models.UniqueConstraint(fields=['visit','url_hash'],name='browser_discovered_identity')]
        indexes=[models.Index(fields=['visit','state'],name='browser_discovered_state')]
