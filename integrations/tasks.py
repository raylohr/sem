from celery import shared_task
from django.core.exceptions import PermissionDenied
from django.db import transaction
from projects.models import Project
from projects.permissions import can_edit, can_manage
from sources.models import ImportBatch
from sources.services import publish_records
from .models import GSCConnection, APIResponsePage
from .gsc import authorized_session, fetch_search_analytics, fetch_inspections, ConnectorError


def check_batch_access(batch_id):
    batch = ImportBatch.objects.select_related("created_by", "project", "gsc_connection__owner").get(pk=batch_id)
    connection = batch.gsc_connection
    if not connection or not connection.active or connection.project_id != batch.project_id:
        raise PermissionDenied
    if not can_edit(batch.created_by, batch.project) or not can_manage(connection.owner, batch.project):
        raise PermissionDenied
    return batch


@shared_task(ignore_result=True)
def process_gsc(batch_id):
    try:
        batch = check_batch_access(batch_id)
        if batch.status == "complete":
            return
        ImportBatch.objects.filter(pk=batch_id).exclude(status="complete").update(status="processing", error="")
        with authorized_session(batch.gsc_connection) as session:
            if batch.request_metadata.get("kind") == "inspection":
                records, pages, warnings = fetch_inspections(session,
                    property_url=batch.request_metadata["property"], urls=batch.request_metadata["urls"],
                    check_access=lambda: check_batch_access(batch_id))
            else:
                records, pages, warnings = fetch_search_analytics(session,
                    property_url=batch.request_metadata["property"], start=batch.period_start, end=batch.period_end,
                    check_access=lambda: check_batch_access(batch_id))
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=batch.project_id)
            batch = ImportBatch.objects.select_for_update().get(pk=batch_id)
            check_batch_access(batch_id)
            if batch.status == "complete":
                return
            from projects.hosts import allows_url
            publish_records(batch_id=batch_id, records=records)
            batch.refresh_from_db()
            for page in pages:
                payload = page['payload']
                if 'rows' in payload:
                    payload['rows'] = [row for row in payload['rows'] if isinstance(row, dict)
                                       and len(row.get('keys', [])) == 2
                                       and allows_url(batch.project, row['keys'][1])]
            pages = [page for page in pages if not page['request_body'].get('inspectionUrl')
                     or allows_url(batch.project, page['request_body']['inspectionUrl'])]
            APIResponsePage.objects.bulk_create([APIResponsePage(batch=batch, **p) for p in pages], batch_size=10)
            batch.warnings = list(dict.fromkeys([*batch.warnings, *warnings]))
            batch.save(update_fields=["warnings"])
    except ImportBatch.DoesNotExist:
        return
    except PermissionDenied:
        ImportBatch.objects.filter(pk=batch_id).exclude(status="complete").update(status="failed", error="Projecttoegang of bronverbinding ingetrokken.")
    except ConnectorError as exc:
        ImportBatch.objects.filter(pk=batch_id).exclude(status="complete").update(status="failed", error=str(exc))
    except Exception:
        # Never log Google's exception payload: it may contain OAuth credentials.
        ImportBatch.objects.filter(pk=batch_id).exclude(status="complete").update(status="failed", error="GSC-import mislukt; probeer opnieuw.")


def enqueue_gsc(batch_id):
    try:
        process_gsc.delay(str(batch_id))
    except Exception:
        # Pending state is retained; the retry endpoint can enqueue again.
        pass


@shared_task(ignore_result=True)
def schedule_daily_gsc():
    """One durable batch per connection/day; pending/failed batches retry on each tick."""
    import datetime
    from zoneinfo import ZoneInfo
    from django.utils import timezone
    from .gsc import configured
    if not configured():
        return
    today = timezone.now().astimezone(ZoneInfo("America/Los_Angeles")).date()
    for connection_id in GSCConnection.objects.filter(active=True, daily_enabled=True).values_list("pk", flat=True):
        with transaction.atomic():
            connection = GSCConnection.objects.select_for_update().select_related("owner", "project").get(pk=connection_id)
            if not connection.active or not connection.daily_enabled or not can_manage(connection.owner, connection.project):
                continue
            if connection.last_scheduled_date != today:
                # A three-day lag and seven-day overlap allow late final data to arrive.
                end = today - datetime.timedelta(days=3)
                ImportBatch.objects.create(project=connection.project, source="gsc", method="api",
                    label=f"GSC dagelijkse import {today}", created_by=connection.owner,
                    period_start=end-datetime.timedelta(days=6), period_end=end,
                    gsc_connection=connection, parser_version="gsc-api-v1",
                    request_metadata={"property": connection.property_url, "scheduled_date": today.isoformat(),
                        "dimensions": ["date", "page"], "type": "web", "dataState": "final",
                        "timezone": "America/Los_Angeles"})
                connection.last_scheduled_date = today
                connection.save(update_fields=["last_scheduled_date"])
    for batch_id in ImportBatch.objects.filter(gsc_connection__active=True,
            gsc_connection__daily_enabled=True, status__in=["pending", "failed"],
            request_metadata__has_key="scheduled_date").values_list("pk", flat=True):
        enqueue_gsc(batch_id)

# Register Project API tasks with Celery autodiscovery.
from .seranking_tasks import process_seranking  # noqa: F401,E402
