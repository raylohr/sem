import uuid
from django.conf import settings
from django.db import models


class Dossier(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    project=models.ForeignKey('projects.Project',on_delete=models.CASCADE,related_name='dossiers')
    crawl=models.ForeignKey('sources.ImportBatch',on_delete=models.CASCADE,related_name='dossiers')
    browser=models.ForeignKey('sources.ImportBatch',null=True,on_delete=models.SET_NULL,related_name='browser_dossiers')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    title=models.CharField(max_length=200)
    status=models.CharField(max_length=16,default='pending')
    version=models.CharField(max_length=40,default='report-v1')
    created_at=models.DateTimeField(auto_now_add=True)
    completed_at=models.DateTimeField(null=True)
    coverage=models.JSONField(default=dict)
    error=models.CharField(max_length=255,blank=True)
    def __str__(self):return self.title
    class Meta:
        indexes=[models.Index(fields=['project','-created_at'],name='report_project_date')]


class DossierInput(models.Model):
    dossier=models.ForeignKey(Dossier,on_delete=models.CASCADE,related_name='inputs')
    run=models.ForeignKey('assessments.AnalysisRun',null=True,on_delete=models.SET_NULL)
    batch=models.ForeignKey('sources.ImportBatch',null=True,on_delete=models.SET_NULL)
    kind=models.CharField(max_length=20)
    snapshot=models.JSONField(default=dict)


class DossierPage(models.Model):
    dossier=models.ForeignKey(Dossier,on_delete=models.CASCADE,related_name='pages')
    source_record=models.ForeignKey('sources.SourceRecord',null=True,on_delete=models.SET_NULL)
    url=models.TextField()
    url_hash=models.CharField(max_length=64)
    scope=models.CharField(max_length=40)
    measured_at=models.DateTimeField(null=True)
    checks=models.JSONField(default=dict)
    snapshot=models.JSONField(default=dict)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['dossier','url_hash','scope'],name='report_page_identity')]


class IssueGroup(models.Model):
    dossier=models.ForeignKey(Dossier,on_delete=models.CASCADE,related_name='groups')
    fingerprint=models.CharField(max_length=64)
    rule=models.CharField(max_length=80)
    category=models.CharField(max_length=30)
    title=models.CharField(max_length=255)
    recommendation=models.TextField()
    severity=models.CharField(max_length=16)
    basis=models.CharField(max_length=100)
    occurrences=models.PositiveIntegerField(default=0)
    affected_urls=models.PositiveIntegerField(default=0)
    def __str__(self):return self.title+' · '+str(self.affected_urls)+' URL’s'
    class Meta:
        constraints=[models.UniqueConstraint(fields=['dossier','fingerprint'],name='report_group_identity')]
        indexes=[models.Index(fields=['dossier','category','severity'],name='report_group_filter')]


class IssueOccurrence(models.Model):
    group=models.ForeignKey(IssueGroup,on_delete=models.CASCADE,related_name='evidence')
    finding=models.ForeignKey('assessments.Finding',null=True,on_delete=models.SET_NULL)
    source_record=models.ForeignKey('sources.SourceRecord',null=True,on_delete=models.SET_NULL)
    url=models.TextField()
    url_hash=models.CharField(max_length=64)
    scope=models.CharField(max_length=40)
    measured_at=models.DateTimeField(null=True)
    snapshot=models.JSONField(default=dict)
    class Meta:
        indexes=[models.Index(fields=['group','url_hash'],name='report_occurrence_url')]


class CategoryScore(models.Model):
    dossier=models.ForeignKey(Dossier,on_delete=models.CASCADE,related_name='categories')
    key=models.CharField(max_length=30)
    title=models.CharField(max_length=100)
    state=models.CharField(max_length=30)
    score=models.PositiveSmallIntegerField(null=True)
    coverage=models.JSONField(default=dict)
    calculation=models.JSONField(default=dict)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['dossier','key'],name='report_category_identity')]


class ActionItem(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    project=models.ForeignKey('projects.Project',on_delete=models.CASCADE,related_name='report_actions')
    title=models.CharField(max_length=255)
    description=models.TextField(blank=True)
    acceptance=models.TextField()
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name='website_actions')
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    status=models.CharField(max_length=20,default='review')
    priority=models.CharField(max_length=10,default='medium')
    effort=models.CharField(max_length=10,default='unknown')
    rationale=models.TextField(blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    groups=models.ManyToManyField(IssueGroup,through='ActionIssue',related_name='actions')
    def __str__(self):return self.title
    class Meta:
        indexes=[models.Index(fields=['project','status','priority'],name='report_action_filter')]


class ActionIssue(models.Model):
    action=models.ForeignKey(ActionItem,on_delete=models.CASCADE)
    group=models.ForeignKey(IssueGroup,on_delete=models.CASCADE)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['action','group'],name='report_action_group')]


class ActionEvent(models.Model):
    action=models.ForeignKey(ActionItem,on_delete=models.CASCADE,related_name='events')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    kind=models.CharField(max_length=30)
    snapshot=models.JSONField(default=dict)


class DossierResource(models.Model):
    """Frozen target controls, kept outside content-page score denominators."""
    dossier=models.ForeignKey(Dossier,on_delete=models.CASCADE,related_name='resources')
    measurement=models.ForeignKey('scans.ResourceMeasurement',null=True,on_delete=models.SET_NULL)
    url=models.TextField()
    url_hash=models.CharField(max_length=64)
    purpose=models.CharField(max_length=12)
    measured_at=models.DateTimeField(null=True)
    checks=models.JSONField(default=dict)
    snapshot=models.JSONField(default=dict)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['dossier','purpose','url_hash'],name='dossier_resource_identity')]


class ActionProposal(models.Model):
    dossier=models.ForeignKey(Dossier,on_delete=models.CASCADE,related_name='proposals')
    fingerprint=models.CharField(max_length=64)
    title=models.CharField(max_length=255)
    discipline=models.CharField(max_length=80)
    problem=models.TextField()
    hypothesis=models.TextField()
    direction=models.TextField()
    acceptance=models.TextField()
    evidence=models.JSONField(default=dict)
    provenance=models.JSONField(default=dict)
    status=models.CharField(max_length=16,default='proposed')
    review=models.JSONField(default=dict)
    action=models.ForeignKey(ActionItem,null=True,on_delete=models.SET_NULL,related_name='proposals')
    groups=models.ManyToManyField(IssueGroup,related_name='proposals')
    class Meta:
        constraints=[models.UniqueConstraint(fields=['dossier','fingerprint'],name='proposal_dossier_identity')]


class ProposalSupplement(models.Model):
    proposal=models.ForeignKey(ActionProposal,on_delete=models.CASCADE,related_name='supplements')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    model=models.CharField(max_length=160)
    prompt_reference=models.CharField(max_length=255)
    text=models.TextField()
    provenance=models.JSONField(default=dict)


class ProposalReviewEvent(models.Model):
    proposal=models.ForeignKey(ActionProposal,on_delete=models.CASCADE,related_name='reviews')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    decision=models.CharField(max_length=16)
    reason=models.TextField(blank=True)
