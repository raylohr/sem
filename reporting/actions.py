"""Project-scoped workflow; only a newer measured pass can resolve an action."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from projects.models import Project
from .models import ActionItem,ActionIssue,ActionEvent,IssueGroup,IssueOccurrence,Dossier,DossierPage,DossierResource
from .services import allowed
from .catalog import BASE

STATUSES={'review':'Te beoordelen','planned':'Gepland','in_progress':'In uitvoering','verify':'Gereed voor controle','resolved':'Opgelost','accepted':'Bewust geaccepteerd'}
PRIORITIES={'high':'Hoog','medium':'Middel','low':'Laag'}
EFFORTS={'unknown':'Onbekend','small':'Klein','medium':'Middel','large':'Groot'}


def validate_values(project,values):
    if values.get('priority','medium') not in PRIORITIES or values.get('effort','unknown') not in EFFORTS:raise ValidationError('Ongeldige prioriteit of inspanning.')
    owner=values.get('owner')
    if owner:allowed(owner,project)
    if not values.get('title','').strip() or not values.get('acceptance','').strip():raise ValidationError('Titel en acceptatiecriterium zijn verplicht.')


@transaction.atomic
def create_action(project,actor,groups,values):
    Project.objects.select_for_update().get(pk=project.pk);allowed(actor,project);validate_values(project,values)
    ids={g.pk if hasattr(g,'pk') else g for g in groups}
    selected=list(IssueGroup.objects.filter(pk__in=ids,dossier__project=project,dossier__status='complete'))
    if not selected or len(selected)!=len(ids):raise ValidationError('Kies uitsluitend gepubliceerde problemen uit dit project.')
    existing=ActionItem.objects.filter(project=project,groups=selected[0]).distinct().first() if len(selected)==1 else None
    if existing:return existing
    action=ActionItem.objects.create(project=project,created_by=actor,**{key:values.get(key,'') for key in ('title','description','acceptance')},
        owner=values.get('owner'),priority=values.get('priority','medium'),effort=values.get('effort','unknown'))
    ActionIssue.objects.bulk_create([ActionIssue(action=action,group=g) for g in selected])
    ActionEvent.objects.create(action=action,actor=actor,kind='created',snapshot={'groups':[g.pk for g in selected],'title':action.title})
    return action


@transaction.atomic
def link_action(action_id,group,actor):
    action=ActionItem.objects.select_for_update().select_related('project').get(pk=action_id)
    allowed(actor,action.project)
    if group.dossier.project_id!=action.project_id or group.dossier.status!='complete':raise ValidationError('Dit probleem hoort niet bij hetzelfde project.')
    _,created=ActionIssue.objects.get_or_create(action=action,group=group)
    if created:
        ActionEvent.objects.create(action=action,actor=actor,kind='linked',snapshot={'groups':[group.pk]})
        if action.status=='resolved' and group.evidence.filter(measured_at__gt=action.updated_at).exists():
            action.status='review';action.save(update_fields=['status','updated_at'])
            ActionEvent.objects.create(action=action,actor=actor,kind='reopened',snapshot={'reason':'Een nieuw gemeten signaal is expliciet aan deze actie gekoppeld.'})
    return action


@transaction.atomic
def update_action(action_id,actor,values,expected_updated_at):
    action=ActionItem.objects.select_for_update().select_related('project').get(pk=action_id)
    allowed(actor,action.project);validate_values(action.project,values)
    if action.updated_at.isoformat()!=expected_updated_at:raise ValidationError('Deze actie is ondertussen gewijzigd. Vernieuw de pagina.')
    status=values.get('status',action.status)
    if status not in STATUSES or status=='resolved' and action.status!='resolved':raise ValidationError('Opgelost vereist controle met een nieuwe meting.')
    rationale=values.get('rationale','').strip()
    if status=='accepted' and len(rationale)<10:raise ValidationError('Bewust accepteren vereist een onderbouwde motivatie van minimaal tien tekens.')
    if status=='resolved' and values.get('acceptance',action.acceptance)!=action.acceptance:
        raise ValidationError('Gewijzigde acceptatiecriteria vereisen opnieuw controle.')
    before={key:getattr(action,key) for key in ('status','priority','effort','title','rationale')};before['owner_id']=action.owner_id
    for key in ('title','description','acceptance','owner','priority','effort','status','rationale'):
        if key in values:setattr(action,key,values[key])
    action.save()
    after={key:getattr(action,key) for key in ('status','priority','effort','title','rationale')};after['owner_id']=action.owner_id
    ActionEvent.objects.create(action=action,actor=actor,kind='updated',snapshot={'before':before,'after':after})
    return action


def check_rule(rule):
    if rule.startswith(('tracking_','policy_')):return 'privacy_tracking'
    if rule.startswith('accessibility_'):
        return {'accessibility_color-contrast':'a11y_contrast','accessibility_link-in-text-block':'a11y_contrast','accessibility_image-alt':'a11y_alt'}.get(rule,'a11y_structure')
    return BASE.get(rule,rule)


def removed_direct_script(old,page,dossier):
    """A complete, newer source inventory is affirmative absence evidence; no finding is not enough."""
    resource=old.snapshot.get('resource',{})
    if not page or not page.source_record_id or not page.measured_at or not old.measured_at or page.measured_at<=old.measured_at:return False
    direct=resource.get('direct_script_reference',False)
    if not direct and old.finding_id:
        direct=any(e.get('source_url')==old.url and e.get('kind')=='script' and not e.get('parent_url') for e in old.finding.details.get('elements',[]))
    if not direct:return False
    data=page.source_record.data
    if data.get('analysis_truncated') or data.get('resource_discovery',{}).get('version')!='resources-v1' or not data.get('resource_discovery',{}).get('complete') or 'resource_candidates' not in data:return False
    if page.scope=='browser':
        from browser_scans.models import BrowserVisit
        if not BrowserVisit.objects.filter(source_record_id=page.source_record_id,status='complete',scenario='baseline').exists():return False
    from .services import url_identity,signature,digest
    import json
    destination=resource.get('destination')
    if not destination:return False
    if any(url_identity(candidate.get('url'))[1]==url_identity(destination)[1] for candidate in data['resource_candidates']):return False
    for rule in ('broken_resource','resource_http_error','resource_mime_mismatch'):
        _,signal=signature(rule,{'destination':destination})
        fingerprint=digest(json.dumps(['group-v1',rule,signal],ensure_ascii=False))
        if dossier.groups.filter(fingerprint=fingerprint).exists():return False
    return True


@transaction.atomic
def verify_action(action_id,dossier_id,actor,acceptance_confirmed=False):
    action=ActionItem.objects.select_for_update().select_related('project').get(pk=action_id);allowed(actor,action.project)
    dossier=Dossier.objects.get(pk=dossier_id,project=action.project,status='complete')
    if not dossier.inputs.exists() or dossier.inputs.filter(run=None).exists() or dossier.coverage.get('limits'):raise ValidationError('Het controledossier heeft ontbrekende of afgekapte brongegevens.')
    original=IssueOccurrence.objects.filter(group__actions=action).select_related('group').order_by('id')
    observations={};undated=set();versions={};resource_versions={};limited=False
    for index,item in enumerate(original.iterator(chunk_size=200)):
        if index>=20000:limited=True;break
        from .services import url_identity
        resource=item.snapshot.get('resource')
        target_hash=url_identity(resource.get('destination'))[1] if resource and resource.get('destination') else None
        key=(item.url_hash,item.scope,check_rule(item.group.rule),target_hash,resource.get('purpose','external' if item.group.rule=='external_http_error' else 'resource') if resource else None)
        if not item.measured_at:undated.add(key)
        if target_hash:
            from assessments.r4 import CONTROL_VERSION
            resource_versions.setdefault(key,set()).add(item.snapshot.get('control_version') or ('resource-controls-v1' if item.snapshot.get('rule_version')=='6' else None))
        versions.setdefault(key,set()).add((item.snapshot.get('definition'),item.snapshot.get('rule_version')))
        previous=observations.get(key)
        if previous is None or item.measured_at and (not previous.measured_at or item.measured_at>previous.measured_at):observations[key]=item
    counts={'pass':0,'remaining':0,'not_remeasured':0};samples=[]
    present_groups=set(dossier.groups.filter(fingerprint__in={item.group.fingerprint for item in observations.values()}).values_list('fingerprint',flat=True))
    keys=list(observations)
    for offset in range(0,len(keys),200):
        subset=keys[offset:offset+200]
        pages={(p.url_hash,p.scope):p for p in DossierPage.objects.filter(dossier=dossier,url_hash__in=[k[0] for k in subset])}
        resources={(r.url_hash,r.purpose):r for r in DossierResource.objects.filter(dossier=dossier,url_hash__in=[k[3] for k in subset if k[3]])}
        present=set(IssueOccurrence.objects.filter(group__dossier=dossier,group__fingerprint__in=[observations[k].group.fingerprint for k in subset],
            url_hash__in=[k[0] for k in subset]).values_list('group__fingerprint','url_hash','scope'))
        for key in subset:
            old=observations[key];page=pages.get((key[0],key[1]));verdict='not_remeasured'
            if key[3]:page=resources.get((key[3],key[4]))
            compatible=page and (bool(key[3]) or page.source_record_id is not None) and key not in undated and all(page.snapshot.get('definitions',{}).get(definition)==version for definition,version in versions[key])
            if key[3] and key[2]=='resource_image_large' and page:
                compatible=compatible and page.snapshot.get('threshold_bytes')==old.snapshot.get('resource',{}).get('threshold_bytes')
            if key[3]:
                from assessments.r4 import CONTROL_VERSION
                original_version=old.snapshot.get('control_version') or ('resource-controls-v1' if old.snapshot.get('rule_version')=='6' else None)
                compatible=page and key not in undated and None not in resource_versions.get(key,{None}) and all(page.snapshot.get('control_versions',{}).get(key[2])==version for version in resource_versions[key])
                if page and key[2]=='resource_image_large':compatible=compatible and page.snapshot.get('threshold_bytes')==old.snapshot.get('resource',{}).get('threshold_bytes')
                source_page=pages.get((key[0],key[1]))
                if page is None and key[2]=='resource_http_error' and key not in undated and removed_direct_script(old,source_page,dossier):verdict='pass'
            if compatible and page.measured_at and old.measured_at and page.measured_at>old.measured_at:
                state=page.checks.get(key[2],'unknown')
                verdict='remaining' if state=='issue' or (old.group.fingerprint,key[0],key[1]) in present else 'pass' if state=='pass' else 'not_remeasured'
            counts[verdict]+=1
            if len(samples)<20:samples.append({'url':old.url,'rule':key[2],'verdict':verdict})
    measured=bool(observations) and not present_groups and not limited and not counts['remaining'] and not counts['not_remeasured']
    resolved=measured and acceptance_confirmed
    if resolved:action.status='resolved'
    elif action.status!='accepted':action.status='verify'
    action.save(update_fields=['status','updated_at'])
    ActionEvent.objects.create(action=action,actor=actor,kind='verification',snapshot={'dossier_id':str(dossier.pk),'counts':counts,
        'limited':limited,'remaining_groups':len(present_groups),'acceptance_confirmed':acceptance_confirmed,'resolved':resolved,'measured_pass':measured,'samples':samples,
        'interpretation':'A missing finding is not proof of resolution; newer measured passing controls and compatible rule versions are required.'})
    return action
