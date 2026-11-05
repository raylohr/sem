"""Deterministic proposals. AI text is an unverified supplement, never measurement evidence."""
import hashlib,json
from urllib.parse import urlsplit
from django.db import transaction
from django.db.models import Count
from django.core.exceptions import ValidationError
from projects.models import Project
from .models import ActionProposal,IssueOccurrence,ProposalSupplement,ProposalReviewEvent
from .services import allowed
from .actions import create_action

VERSION='action-templates-v1'


def build(dossier):
    proposals=[];sdk=[];ordinary=[];inputs=list(dossier.inputs.values('snapshot'))
    groups=list(dossier.groups.order_by('id')[:10000])
    for group in groups:
        sample=group.evidence.order_by('id').values('id','url','snapshot').first()
        resource=(sample or {}).get('snapshot',{}).get('resource',{})
        if group.rule=='resource_http_error' and resource.get('http_status')==404 and urlsplit(resource.get('destination','')).path.startswith('/_sdk/') and urlsplit(resource.get('destination','')).path.endswith('.js'):
            sdk.append((group,sample))
        else:ordinary.append((group,sample))
    bundle=[]
    if len(sdk)==4:
        sources=[set(group.evidence.values_list('url',flat=True)) for group,sample in sdk]
        common=set.intersection(*sources)
        bundle=[row for row in sdk] if any('bereken' in url or 'calculator' in url for url in common) else []
    if bundle:proposals.append(draft(dossier,bundle,calculator=True,inputs=inputs))
    else:ordinary.extend(sdk)
    proposals.extend(draft(dossier,[row],inputs=inputs) for row in ordinary)
    for proposal,selected in proposals:
        proposal.save();proposal.groups.set(selected)


def draft(dossier,rows,calculator=False,inputs=None):
    groups=[row[0] for row in rows];first=groups[0];rule=first.rule
    discipline='Webontwikkeling / SEO'
    if first.category=='content':discipline='Content / SEO'
    elif first.category=='experience':discipline='Frontend / toegankelijkheid'
    elif first.category=='privacy':discipline='Privacy- en toestemmingsspecialist'
    elif first.category=='technology':discipline='Technisch beheer / leverancier'
    examples=[{'group_id':g.pk,'rule':g.rule,'source_url':(sample or {}).get('url'),'occurrence_id':(sample or {}).get('id'),'summary':(sample or {}).get('snapshot',{}).get('summary',[])} for g,sample in rows]
    title='Herstel de vier SDK-verwijzingen in de calculator' if calculator else first.title
    hypothesis='Mogelijk gedeelde invoegcode of integratie van de calculator. Controleer de implementatie; dezelfde oorzaak is nog niet bewezen.' if calculator else 'Gedeelde template, configuratie of inhoud kan de signalen verklaren; oorzaak nog te bevestigen aan de hand van het bewijs.'
    direction='Bepaal of de vier SDK’s nodig zijn. Herstel zo nodig juiste script-URL’s en JavaScript-antwoorden; verwijder anders de overbodige directe verwijzingen en controleer de calculatorwerking.' if calculator else first.recommendation
    acceptance='Een nieuwe meting bevestigt geslaagde betrokken controles en de verantwoordelijke heeft werking en acceptatiecriteria beoordeeld.'
    if rule.startswith('resource_') or calculator:acceptance='Bij een nieuwe meting is elk behouden doel bereikbaar met het passende bestandstype. Voor verwijderde directe scripts bevestigt een volledige nieuwe broninventaris dat de verwijzingen weg zijn. Controleer ook de functionaliteit in de browser; afwezigheid van alleen een melding is onvoldoende.'
    elif rule=='image_empty_alt_review':acceptance='Beoordeel de afbeelding in context: decoratie behoudt alt="", informatieve inhoud krijgt passende tekst. Leg bewuste uitzonderingen onderbouwd vast en controleer nieuwe bron- en browsermetingen.'
    elif rule.startswith('structured_'):acceptance='Nieuwe markup slaagt afzonderlijk voor relevante syntaxis-, vocabulaire- en gekozen profielcontroles; controleer inhoud en actuele zoekmachinevoorwaarden. Geen automatische weergavegarantie.'
    elif rule in ('main_content_thin','content_readability_review'):acceptance='De inhoud sluit aantoonbaar aan op doelgroep en paginadoel. Leg expertbeoordeling vast; voeg niet uitsluitend woorden toe om een drempel te halen en controleer de gekozen taal-/selectiecontext opnieuw.'
    identity=hashlib.sha256(json.dumps([VERSION,sorted(g.fingerprint for g in groups)]).encode()).hexdigest()
    source_refs=inputs if inputs is not None else list(dossier.inputs.values('snapshot'))
    evidence={'examples':examples,'affected_urls':IssueOccurrence.objects.filter(group__in=groups).aggregate(n=Count('url_hash',distinct=True))['n'],
              'group_ids':[g.pk for g in groups],'technical_severities':sorted({g.severity for g in groups}),
              'status':'observed signals; cause is hypothesis; business priority and owner require user choice'}
    provenance={'engine':'rules','template_version':VERSION,'inputs':source_refs,'input_sha256':hashlib.sha256(json.dumps(evidence,sort_keys=True,default=str).encode()).hexdigest()}
    return ActionProposal(dossier=dossier,fingerprint=identity,title=title,discipline=discipline,
        problem=('Vier afzonderlijk gemeten script-404’s op dezelfde calculator.' if calculator else first.title),hypothesis=hypothesis,direction=direction,
        acceptance=acceptance,evidence=evidence,provenance=provenance),groups


@transaction.atomic
def approve(proposal,actor,values):
    Project.objects.select_for_update().get(pk=proposal.dossier.project_id);allowed(actor,proposal.dossier.project)
    proposal=ActionProposal.objects.select_for_update().get(pk=proposal.pk)
    if proposal.action_id:return proposal.action
    action=create_action(proposal.dossier.project,actor,list(proposal.groups.all()),values)
    from django.utils import timezone
    proposal.review={'actor_id':actor.pk,'at':timezone.now().isoformat(),'decision':'approved'}
    ProposalReviewEvent.objects.create(proposal=proposal,actor=actor,decision='approved')
    proposal.action=action;proposal.status='approved';proposal.save(update_fields=['action','status','review'])
    return action


@transaction.atomic
def supplement(proposal,actor,values):
    Project.objects.select_for_update().get(pk=proposal.dossier.project_id);allowed(actor,proposal.dossier.project)
    if proposal.supplements.count()>=20:raise ValidationError('Maximaal twintig aanvullingen per voorstel.')
    model=values.get('model','').strip();prompt=values.get('prompt_reference','').strip();text=values.get('text','').strip()
    if not model or len(model)>160 or not prompt or len(prompt)>255 or not text or len(text)>4000:raise ValidationError('Model, promptreferentie en begrensde aanvulling zijn verplicht.')
    return ProposalSupplement.objects.create(proposal=proposal,actor=actor,model=model,prompt_reference=prompt,text=text,
        provenance={'status':'unverified_user_import','input_sha256':proposal.provenance['input_sha256'],'group_ids':proposal.evidence['group_ids'],
                    'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'scope':'supplement only; cannot change facts, scores, owners or resolution; no model invocation attested'})
