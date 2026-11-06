"""Derived, rebuildable crawl data and versioned evidence-backed analyses."""
import uuid
from django.db import models


class PageMeasurement(models.Model):
    batch = models.ForeignKey('sources.ImportBatch', on_delete=models.CASCADE, related_name='page_measurements')
    source_record = models.OneToOneField('sources.SourceRecord', on_delete=models.CASCADE)
    url = models.ForeignKey('catalog.ProjectURL', on_delete=models.CASCADE)
    measured_at = models.DateTimeField(null=True)
    http_status = models.PositiveSmallIntegerField(null=True)
    final_http_status = models.PositiveSmallIntegerField(null=True)
    final_url = models.TextField()
    content_type = models.CharField(max_length=255, blank=True)
    is_indexable = models.BooleanField(null=True)
    word_count = models.PositiveIntegerField(null=True)
    response_time_ms = models.PositiveIntegerField(null=True)
    depth = models.PositiveIntegerField(null=True)
    text_hash = models.CharField(max_length=64, blank=True)
    extractor_version = models.CharField(max_length=40)
    extracted_at = models.DateTimeField(auto_now_add=True)
    facts = models.JSONField(default=dict)
    coverage = models.JSONField(default=dict)

    class Meta:
        indexes = [models.Index(fields=['batch', 'url'], name='assessment_page_url'), models.Index(fields=['batch', 'http_status', 'id'], name='assessment_page_status'),
                   models.Index(fields=['batch', 'text_hash'], name='assessment_page_hash'),
                   models.Index(fields=['url', '-measured_at', '-id'], name='assessment_page_history')]


class LinkObservation(models.Model):
    source = models.ForeignKey(PageMeasurement, on_delete=models.CASCADE, related_name='links')
    position = models.PositiveIntegerField()
    destination = models.TextField()
    destination_hash = models.CharField(max_length=64)
    anchor_text = models.TextField(null=True)
    rel = models.JSONField(null=True)
    locator = models.CharField(max_length=100, blank=True)
    discovery_mode = models.CharField(max_length=24, default='server')
    element_path=models.CharField(max_length=1024,blank=True)
    region=models.CharField(max_length=24,blank=True)
    target=models.CharField(max_length=80,blank=True)
    details=models.JSONField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['source', 'position'], name='assessment_link_position')]
        indexes = [models.Index(fields=['destination_hash', 'source'], name='assessment_link_target')]


class AnalysisDefinition(models.Model):
    key = models.CharField(max_length=100)
    version = models.CharField(max_length=40)
    question = models.TextField()
    method = models.CharField(max_length=16, default='rules')
    output_schema = models.JSONField(default=dict)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['key', 'version'], name='assessment_definition_version')]


class AnalysisRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey('sources.ImportBatch', on_delete=models.CASCADE, related_name='analysis_runs')
    definition = models.ForeignKey(AnalysisDefinition, on_delete=models.PROTECT)
    extractor_version = models.CharField(max_length=40)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=16, default='processing')
    coverage = models.JSONField(default=dict)
    summary = models.JSONField(default=dict)
    class Meta:
        indexes = [models.Index(fields=['batch', '-started_at'], name='assessment_run_batch')]


class AnalysisResult(models.Model):
    run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name='results')
    source_record = models.ForeignKey('sources.SourceRecord', on_delete=models.CASCADE)
    outcome = models.CharField(max_length=16)  # issue / pass / unknown / not_applicable
    answer = models.JSONField(default=dict)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['run', 'source_record'], name='assessment_result_record')]


class Finding(models.Model):
    result = models.ForeignKey(AnalysisResult, on_delete=models.CASCADE, related_name='findings')
    rule = models.CharField(max_length=80)
    severity = models.CharField(max_length=16)
    title = models.CharField(max_length=255)
    recommendation = models.TextField()
    review_status = models.CharField(max_length=16, default='unreviewed')
    details = models.JSONField(default=dict)
    class Meta:
        indexes = [models.Index(fields=['rule', 'severity', 'id'], name='assessment_finding_rule')]


class FindingEvidence(models.Model):
    finding = models.ForeignKey(Finding, on_delete=models.CASCADE, related_name='evidence')
    source_record = models.ForeignKey('sources.SourceRecord', on_delete=models.CASCADE)
    document = models.ForeignKey('scans.ScanDocument', null=True, on_delete=models.SET_NULL)
    browser_artifact = models.ForeignKey('browser_scans.BrowserArtifact', null=True, on_delete=models.SET_NULL)
    role = models.CharField(max_length=16)
    snapshot = models.JSONField(default=dict)


class ReprocessingJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey('sources.ImportBatch', on_delete=models.CASCADE, related_name='assessment_jobs')
    actor = models.ForeignKey('accounts.User', on_delete=models.PROTECT)
    status = models.CharField(max_length=16, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)
    run = models.ForeignKey(AnalysisRun, null=True, on_delete=models.SET_NULL)
    error = models.CharField(max_length=255, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['batch'], condition=models.Q(status__in=['pending', 'processing']), name='assessment_active_job')]


class ReferenceObservation(models.Model):
    source=models.ForeignKey(PageMeasurement,on_delete=models.CASCADE,related_name='references')
    analysis_run=models.ForeignKey(AnalysisRun,null=True,on_delete=models.CASCADE,related_name='references')
    identity=models.CharField(max_length=64)
    url=models.TextField()
    destination=models.CharField(max_length=24)
    function=models.CharField(max_length=24)
    scope=models.CharField(max_length=24)
    outcome=models.CharField(max_length=24)
    reason=models.CharField(max_length=80,blank=True)
    details=models.JSONField(default=dict)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['source','identity'],name='reference_source_identity')]
        indexes=[models.Index(fields=['analysis_run','destination','function'],name='reference_axes'),models.Index(fields=['analysis_run','outcome'],name='reference_outcome')]
