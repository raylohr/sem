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
    discovered_batch = models.ForeignKey('sources.ImportBatch', null=True, on_delete=models.SET_NULL)
    example_url = models.CharField(max_length=4096, blank=True)
    source_url = models.CharField(max_length=4096, blank=True)
    purpose = models.CharField(max_length=20, default='navigation')
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['project','host'],name='unique_project_host_policy')]
