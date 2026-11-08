"""Invalidate the read model inside the same transaction as source changes.

QuerySet bulk operations also invalidate via SourceRecordQuerySet.
Direct SQL maintenance must explicitly call invalidate(project_id).
"""
from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from projects.models import Project
from .models import ImportBatch, SourceRecord, InventoryExport
from .inventory_index import invalidate


@receiver(pre_save, sender=ImportBatch)
def remember_batch_visibility(sender, instance, **kwargs):
    instance._inventory_was_published = not instance._state.adding and instance.status not in ('complete', 'incomplete') and ImportBatch.objects.filter(
        pk=instance.pk, status__in=('complete', 'incomplete')).exists()


@receiver(post_save, sender=ImportBatch)
@receiver(post_delete, sender=ImportBatch)
def batch_changed(sender, instance, **kwargs):
    if (instance.status in ('complete', 'incomplete') or getattr(instance, '_inventory_was_published', False)) and Project.objects.filter(pk=instance.project_id).exists():
        invalidate(instance.project_id)


@receiver(post_save, sender=SourceRecord)
def record_changed(sender, instance, origin=None, **kwargs):
    project_id = ImportBatch.objects.filter(pk=instance.batch_id, status__in=('complete', 'incomplete')).values_list('project_id', flat=True).first()
    if project_id: invalidate(project_id)


@receiver(pre_save, sender=Project)
def remember_host_rules(sender, instance, **kwargs):
    instance._inventory_previous_hosts = None if instance._state.adding else Project.objects.filter(pk=instance.pk).values_list(
        'domain', 'website', 'include_subdomains').first()


@receiver(post_save, sender=Project)
def project_changed(sender, instance, **kwargs):
    if getattr(instance, '_inventory_previous_hosts', None) != (instance.domain, instance.website, instance.include_subdomains):
        invalidate(instance.pk)


@receiver(post_delete, sender=InventoryExport)
def export_deleted(sender, instance, **kwargs):
    from django.db import transaction
    name = instance.file.name or f'private-exports/{instance.pk}.xlsx'
    storage = instance.file.storage
    transaction.on_commit(lambda: storage.delete(name))
