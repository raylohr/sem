"""Durable daily/weekly wall-clock schedules in Europe/Amsterdam."""
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from celery import shared_task
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.policy import lock_access_changes
from projects.permissions import can_manage
from .models import PlaywrightCrawlSettings, PlaywrightCrawl
from .crawl_service import ACTIVE, start_crawl

ZONE=ZoneInfo('Europe/Amsterdam')


def next_occurrence(settings,after=None):
    now=after or timezone.now()
    local=now.astimezone(ZONE)
    for offset in range(9):
        day=local.date()+timedelta(days=offset)
        if settings.schedule_frequency=='weekly' and day.weekday()!=settings.schedule_weekday:
            continue
        candidate=datetime.combine(day,settings.schedule_time,tzinfo=ZONE)
        # Normalize nonexistent spring times forwards; choose the first autumn occurrence.
        candidate=candidate.astimezone(dt_timezone.utc).astimezone(ZONE)
        if candidate.astimezone(dt_timezone.utc)>now.astimezone(dt_timezone.utc):
            return candidate
    raise ValueError('No future schedule occurrence')


def configure_schedule(settings,actor,before):
    keys=('schedule_enabled','schedule_frequency','schedule_time','schedule_weekday')
    changed=any(before.get(key)!=getattr(settings,key) for key in keys)
    if not settings.schedule_enabled:
        settings.next_run_at=None
        settings.schedule_note=''
        settings.schedule_owner=None
    elif changed or not settings.next_run_at or not settings.schedule_owner_id:
        settings.schedule_owner=actor
        settings.next_run_at=next_occurrence(settings)
        settings.schedule_note=''
    # An administrator explicitly saving enabled scheduling takes responsibility.
    else:
        settings.schedule_owner=actor


@shared_task(ignore_result=True)
def schedule_playwright_crawls():
    due=list(PlaywrightCrawlSettings.objects.filter(schedule_enabled=True,next_run_at__lte=timezone.now()).values_list('pk',flat=True))
    for pk in due:
        with transaction.atomic():
            lock_access_changes()
            config=PlaywrightCrawlSettings.objects.select_for_update(of=('self',)).select_related('project','schedule_owner').filter(pk=pk).first()
            now=timezone.now()
            if not config or not config.schedule_enabled or not config.next_run_at or config.next_run_at>now:
                continue
            owner=config.schedule_owner
            if not owner or not can_manage(owner,config.project):
                config.schedule_enabled=False
                config.next_run_at=None
                config.schedule_note='Herhaling uitgeschakeld: de planner heeft geen actieve projectbeheerrechten.'
            else:
                scheduled_for=config.next_run_at
                config.next_run_at=next_occurrence(config,now)
                if PlaywrightCrawl.objects.filter(project=config.project,status__in=ACTIVE).exists():
                    config.schedule_note='Geplande beurt overgeslagen: er is al een actieve of gepauzeerde crawl.'
                else:
                    try:
                        # Savepoint keeps invalid settings from aborting the scheduler transaction.
                        with transaction.atomic():
                            crawl=start_crawl(actor=owner,project_id=config.project_id)
                            crawl.batch.request_metadata={**crawl.batch.request_metadata,'trigger':'schedule',
                                'scheduled_for':scheduled_for.isoformat()}
                            crawl.batch.save(update_fields=['request_metadata'])
                        config.last_scheduled_at=now
                        config.schedule_note='Geplande crawl gestart.'
                    except (ValidationError,PermissionDenied):
                        config.schedule_note='Geplande crawl niet gestart. Controleer instellingen en projectbeheerrechten.'
            config.save(update_fields=['schedule_enabled','next_run_at','last_scheduled_at','schedule_note'])
