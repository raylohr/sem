import json
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.paginator import Paginator
from django.db.models import Count,Case,When,IntegerField
from django.http import HttpResponse,StreamingHttpResponse
from django.shortcuts import get_object_or_404,render,redirect
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET,require_POST,require_http_methods
from core.ui import project_context
from .models import Dossier,IssueGroup,ActionItem,CategoryScore
from .forms import DossierForm,ActionForm,VerificationForm,LinkActionForm
from .services import create_dossier,enqueue
from .actions import create_action,update_action,verify_action,link_action,STATUSES
from .catalog import CATEGORIES


def context(request,project_id):return project_context(request,project_id,'reports')


def ordered_groups(query):
    return query.annotate(severity_rank=Case(When(severity='high',then=0),When(severity='medium',then=1),default=2,output_field=IntegerField())).order_by('severity_rank','-affected_urls','rule','id')


def dossier_context(request,project_id,dossier_id):
    ctx=context(request,project_id)
    dossier=get_object_or_404(Dossier.objects.select_related('crawl','browser','actor'),pk=dossier_id,project=ctx['project'])
    ctx.update(dossier=dossier,categories=dossier.categories.order_by('id'),inputs=dossier.inputs.select_related('run','batch'),
        missing_sources=dossier.inputs.filter(run=None).exists() or dossier.pages.filter(source_record=None).exists(),action_count=ctx['project'].report_actions.exclude(status__in=('resolved','accepted')).count())
    return ctx


@never_cache
@login_required
@require_GET
def overview(request,project_id):
    ctx=context(request,project_id)
    ctx.update(page=Paginator(Dossier.objects.filter(project=ctx['project']).order_by('-created_at'),25).get_page(request.GET.get('page')))
    return render(request,'reporting/overview.html',ctx)


@never_cache
@login_required
@require_http_methods(['GET','POST'])
def start(request,project_id):
    ctx=context(request,project_id)
    if not ctx['can_edit']:raise PermissionDenied
    form=DossierForm(request.POST if request.method=='POST' else None,project=ctx['project'])
    if request.method=='POST' and form.is_valid():
        try:
            dossier=create_dossier(ctx['project'],request.user,form.cleaned_data['crawl'].pk,
                form.cleaned_data['browser'].pk if form.cleaned_data.get('browser') else None,form.cleaned_data['title'])
            return redirect('ui:reports:dossier',project_id=project_id,dossier_id=dossier.pk)
        except ValidationError as exc:form.add_error(None,exc)
    ctx.update(form=form,title='Onderzoeksdossier maken')
    return render(request,'reporting/form.html',ctx,status=400 if request.method=='POST' else 200)


@never_cache
@login_required
@require_GET
def detail(request,project_id,dossier_id):
    ctx=dossier_context(request,project_id,dossier_id);groups=ctx['dossier'].groups.all()
    category=request.GET.get('category','');severity=request.GET.get('severity','');query=request.GET.get('q','').strip()[:200]
    if category in CATEGORIES:groups=groups.filter(category=category)
    if severity in ('high','medium','low'):groups=groups.filter(severity=severity)
    if query:groups=groups.filter(title__icontains=query)
    filters=request.GET.copy();filters.pop('page',None)
    ctx.update(proposal_page=Paginator(ctx['dossier'].proposals.order_by('id'),25).get_page(request.GET.get('proposal_page')),page=Paginator(ordered_groups(groups),25).get_page(request.GET.get('page')),category=category,severity=severity,query=query,
        category_choices=CATEGORIES.items(),filter_query=filters.urlencode())
    return render(request,'reporting/dossier.html',ctx)


@never_cache
@login_required
@require_GET
def category_detail(request,project_id,dossier_id,category_key):
    ctx=dossier_context(request,project_id,dossier_id)
    item=get_object_or_404(CategoryScore,dossier=ctx['dossier'],key=category_key)
    ctx.update(category=item,rules=[{'key':key,**values} for key,values in item.calculation.get('per_rule',{}).items()])
    return render(request,'reporting/category.html',ctx)


@never_cache
@login_required
@require_POST
def retry(request,project_id,dossier_id):
    ctx=dossier_context(request,project_id,dossier_id)
    if not ctx['can_edit']:raise PermissionDenied
    if Dossier.objects.filter(pk=dossier_id,status='failed').update(status='pending',error=''):enqueue(dossier_id)
    return redirect('ui:reports:dossier',project_id=project_id,dossier_id=dossier_id)


@never_cache
@login_required
@require_GET
def group_detail(request,project_id,group_id):
    ctx=context(request,project_id)
    group=get_object_or_404(IssueGroup.objects.select_related('dossier'),pk=group_id,dossier__project=ctx['project'],dossier__status='complete')
    ctx.update(group=group,page=Paginator(group.evidence.select_related('finding','source_record__batch').order_by('url','id'),25).get_page(request.GET.get('page')),
        actions=group.actions.filter(project=ctx['project']).select_related('owner'),link_form=LinkActionForm(project=ctx['project']))
    return render(request,'reporting/group.html',ctx)


@never_cache
@login_required
@require_POST
def group_link(request,project_id,group_id):
    ctx=context(request,project_id)
    if not ctx['can_edit']:raise PermissionDenied
    group=get_object_or_404(IssueGroup.objects.select_related('dossier'),pk=group_id,dossier__project=ctx['project'],dossier__status='complete')
    form=LinkActionForm(request.POST,project=ctx['project'])
    if form.is_valid():
        link_action(form.cleaned_data['action'].pk,group,request.user)
        return redirect('ui:reports:action',project_id=project_id,action_id=form.cleaned_data['action'].pk)
    ctx.update(form=form,title='Bestaande actie koppelen')
    return render(request,'reporting/form.html',ctx,status=400)


@never_cache
@login_required
@require_GET
def prospect(request,project_id,dossier_id):
    ctx=dossier_context(request,project_id,dossier_id)
    if ctx['dossier'].status!='complete':return redirect('ui:reports:dossier',project_id=project_id,dossier_id=dossier_id)
    groups=list(ordered_groups(ctx['dossier'].groups.all())[:5])
    for group in groups:group.example=group.evidence.order_by('id').values('url','snapshot').first()
    from django.db.models import Min,Case,When,IntegerField
    proposals=ctx['dossier'].proposals.exclude(status='dismissed').annotate(technical_rank=Min(Case(When(groups__severity='high',then=0),When(groups__severity='medium',then=1),default=2,output_field=IntegerField()))).order_by('technical_rank','id')[:5]
    ctx.update(top_proposals=proposals,origin=request.build_absolute_uri('/').rstrip('/'),top_groups=groups)
    if request.GET.get('download')=='1':
        response=HttpResponse(render_to_string('reporting/prospect_document.html',ctx,request=request),content_type='text/html; charset=utf-8')
        response['Content-Disposition']='attachment; filename="websiteonderzoek-'+str(dossier_id)+'.html"'
        response['X-Content-Type-Options']='nosniff';response['Content-Security-Policy']="default-src 'none'; style-src 'unsafe-inline'; sandbox"
        return response
    return render(request,'reporting/prospect.html',ctx)


@never_cache
@login_required
@require_GET
def export(request,project_id,dossier_id):
    ctx=dossier_context(request,project_id,dossier_id);dossier=ctx['dossier']
    def rows():
        yield json.dumps({'type':'dossier','id':str(dossier.pk),'version':dossier.version,'status':dossier.status,'coverage':dossier.coverage,'actions_exported_at':timezone.now().isoformat()},ensure_ascii=False)+'\n'
        for item in dossier.inputs.all():yield json.dumps({'type':'input','snapshot':item.snapshot})+'\n'
        for item in dossier.categories.all():yield json.dumps({'type':'category','key':item.key,'state':item.state,'score':item.score,'coverage':item.coverage,'calculation':item.calculation},ensure_ascii=False)+'\n'
        for resource in dossier.resources.iterator(chunk_size=200):yield json.dumps({'type':'resource','url':resource.url,'purpose':resource.purpose,'measured_at':resource.measured_at.isoformat() if resource.measured_at else None,'checks':resource.checks,'snapshot':resource.snapshot},ensure_ascii=False)+'\n'
        for page in dossier.pages.iterator(chunk_size=200):yield json.dumps({'type':'page','url':page.url,'scope':page.scope,'measured_at':page.measured_at.isoformat() if page.measured_at else None,'checks':page.checks,'snapshot':page.snapshot},ensure_ascii=False)+'\n'
        for group in dossier.groups.prefetch_related('actions').iterator(chunk_size=100):
            yield json.dumps({'type':'group','id':group.pk,'fingerprint':group.fingerprint,'rule':group.rule,'category':group.category,'title':group.title,'severity':group.severity,
                'occurrences':group.occurrences,'affected_urls':group.affected_urls,'basis':group.basis,'actions':[str(a.pk) for a in group.actions.all() if a.project_id==dossier.project_id]},ensure_ascii=False)+'\n'
        for proposal in dossier.proposals.prefetch_related('groups').iterator(chunk_size=100):
            yield json.dumps({'type':'action_proposal','id':proposal.pk,'title':proposal.title,'discipline':proposal.discipline,'problem':proposal.problem,'hypothesis':proposal.hypothesis,'direction':proposal.direction,'acceptance':proposal.acceptance,'status':proposal.status,'action_id':str(proposal.action_id) if proposal.action_id else None,'groups':[g.pk for g in proposal.groups.all()],'evidence':proposal.evidence,'provenance':proposal.provenance,'review':proposal.review},ensure_ascii=False)+'\n'
            for supplement in proposal.supplements.all().order_by('id'):
                yield json.dumps({'type':'unverified_ai_supplement','proposal_id':proposal.pk,'model':supplement.model,'prompt_reference':supplement.prompt_reference,'text':supplement.text,'provenance':supplement.provenance},ensure_ascii=False)+'\n'
            for review in proposal.reviews.all().order_by('id'):
                yield json.dumps({'type':'proposal_review','proposal_id':proposal.pk,'actor_id':review.actor_id,'created_at':review.created_at.isoformat(),'decision':review.decision,'reason':review.reason},ensure_ascii=False)+'\n'
        from .models import IssueOccurrence
        for item in IssueOccurrence.objects.filter(group__dossier=dossier).iterator(chunk_size=200):yield json.dumps({'type':'evidence','group':item.group_id,'finding':item.finding_id,'url':item.url,'scope':item.scope,'measured_at':item.measured_at.isoformat() if item.measured_at else None,'snapshot':item.snapshot},ensure_ascii=False)+'\n'
        actions=ActionItem.objects.filter(project=ctx['project'],groups__dossier=dossier).distinct()
        for action in actions.iterator(chunk_size=100):
            yield json.dumps({'type':'action','id':str(action.pk),'title':action.title,'description':action.description,'acceptance':action.acceptance,
                'status':action.status,'priority':action.priority,'effort':action.effort,'owner_id':action.owner_id,'rationale':action.rationale,'updated_at':action.updated_at.isoformat()},ensure_ascii=False)+'\n'
        from .models import ActionEvent
        for event in ActionEvent.objects.filter(action__in=actions).iterator(chunk_size=100):
            yield json.dumps({'type':'action_event','action_id':str(event.action_id),'kind':event.kind,'actor_id':event.actor_id,'created_at':event.created_at.isoformat(),'snapshot':event.snapshot},ensure_ascii=False)+'\n'
    response=StreamingHttpResponse(rows(),content_type='application/x-ndjson; charset=utf-8')
    response['Content-Disposition']='attachment; filename="dossier-'+str(dossier_id)+'.jsonl"'
    return response


@never_cache
@login_required
@require_GET
def action_list(request,project_id):
    ctx=context(request,project_id);actions=ActionItem.objects.filter(project=ctx['project']).select_related('owner')
    status=request.GET.get('status','')
    if status in STATUSES:actions=actions.filter(status=status)
    ctx.update(page=Paginator(actions.order_by('-updated_at'),25).get_page(request.GET.get('page')),statuses=STATUSES.items(),selected_status=status)
    return render(request,'reporting/actions.html',ctx)


@never_cache
@login_required
@require_http_methods(['GET','POST'])
def action_create(request,project_id):
    ctx=context(request,project_id)
    if not ctx['can_edit']:raise PermissionDenied
    initial={}
    if request.GET.get('group'):
        group=get_object_or_404(IssueGroup,pk=request.GET['group'],dossier__project=ctx['project'],dossier__status='complete')
        initial={'groups':[group.pk],'title':group.title,'description':group.recommendation,'acceptance':'De relevante controle slaagt bij een nieuwe meting en de oplossing is beoordeeld.','priority':'medium','effort':'unknown'}
    form=ActionForm(request.POST if request.method=='POST' else None,project=ctx['project'],initial=initial)
    if request.method=='POST' and form.is_valid():
        try:
            action=create_action(ctx['project'],request.user,form.cleaned_data['groups'],form.cleaned_data)
            return redirect('ui:reports:action',project_id=project_id,action_id=action.pk)
        except ValidationError as exc:form.add_error(None,exc)
    ctx.update(form=form,title='Actie maken')
    return render(request,'reporting/form.html',ctx,status=400 if request.method=='POST' else 200)


@never_cache
@login_required
@require_GET
def action_detail(request,project_id,action_id):
    ctx=context(request,project_id);action=get_object_or_404(ActionItem.objects.select_related('owner'),pk=action_id,project=ctx['project'])
    ctx.update(action=action,events=Paginator(action.events.select_related('actor').order_by('-created_at'),25).get_page(request.GET.get('page')),
        groups=action.groups.filter(dossier__project=ctx['project']).select_related('dossier'),verification_form=VerificationForm(project=ctx['project']))
    return render(request,'reporting/action.html',ctx)


@never_cache
@login_required
@require_http_methods(['GET','POST'])
def action_edit(request,project_id,action_id):
    ctx=context(request,project_id)
    if not ctx['can_edit']:raise PermissionDenied
    action=get_object_or_404(ActionItem,pk=action_id,project=ctx['project'])
    form=ActionForm(request.POST if request.method=='POST' else None,project=ctx['project'],instance=action)
    if request.method=='POST' and form.is_valid():
        try:
            update_action(action.pk,request.user,form.cleaned_data,form.cleaned_data['revision'])
            return redirect('ui:reports:action',project_id=project_id,action_id=action.pk)
        except ValidationError as exc:form.add_error(None,exc)
    ctx.update(form=form,title='Actie wijzigen')
    return render(request,'reporting/form.html',ctx,status=400 if request.method=='POST' else 200)


@never_cache
@login_required
@require_POST
def verify(request,project_id,action_id):
    ctx=context(request,project_id)
    if not ctx['can_edit']:raise PermissionDenied
    action=get_object_or_404(ActionItem,pk=action_id,project=ctx['project'])
    form=VerificationForm(request.POST,project=ctx['project'])
    if form.is_valid():
        try:
            verify_action(action.pk,form.cleaned_data['dossier'].pk,request.user,form.cleaned_data['acceptance_confirmed'])
            return redirect('ui:reports:action',project_id=project_id,action_id=action.pk)
        except ValidationError as exc:form.add_error(None,exc)
    ctx.update(form=form,title='Oplossing controleren')
    return render(request,'reporting/form.html',ctx,status=400)


@never_cache
@login_required
@require_GET
def proposal_detail(request,project_id,proposal_id):
    from .models import ActionProposal
    from .forms import SupplementForm
    ctx=context(request,project_id)
    proposal=get_object_or_404(ActionProposal.objects.select_related('dossier','action'),pk=proposal_id,dossier__project=ctx['project'],dossier__status='complete')
    ctx.update(reviews=proposal.reviews.select_related('actor').order_by('-created_at')[:50],proposal=proposal,groups=proposal.groups.order_by('id'),supplements=proposal.supplements.select_related('actor').order_by('-created_at')[:20],supplement_form=SupplementForm())
    return render(request,'reporting/proposal.html',ctx)


@never_cache
@login_required
@require_http_methods(['GET','POST'])
def proposal_approve(request,project_id,proposal_id):
    from .models import ActionProposal
    from .proposals import approve
    ctx=context(request,project_id)
    if not ctx['can_edit']:raise PermissionDenied
    proposal=get_object_or_404(ActionProposal,pk=proposal_id,dossier__project=ctx['project'],dossier__status='complete')
    initial={'groups':list(proposal.groups.values_list('id',flat=True)),'title':proposal.title,'description':proposal.problem+'\n\n'+proposal.direction+'\n\nVermoedelijke oorzaak: '+proposal.hypothesis+'\nDiscipline: '+proposal.discipline,
             'acceptance':proposal.acceptance,'priority':'medium','effort':'unknown'}
    form=ActionForm(request.POST if request.method=='POST' else None,project=ctx['project'],initial=initial)
    if request.method=='POST' and form.is_valid():
        if set(form.cleaned_data['groups'].values_list('pk',flat=True))!=set(initial['groups']):form.add_error('groups','Bevestig de volledige, getoonde bewijsbundel; losse groepen kunnen via Actie maken.')
        else:
            try:action=approve(proposal,request.user,form.cleaned_data)
            except ValidationError as exc:form.add_error(None,exc)
            else:return redirect('ui:reports:action',project_id=project_id,action_id=action.pk)
    ctx.update(form=form,title='Herstelvoorstel beoordelen en actie maken')
    return render(request,'reporting/form.html',ctx,status=400 if request.method=='POST' else 200)


@never_cache
@login_required
@require_POST
def proposal_supplement(request,project_id,proposal_id):
    from .models import ActionProposal
    from .proposals import supplement
    from .forms import SupplementForm
    from django.http import JsonResponse
    ctx=context(request,project_id)
    if not ctx['can_edit']:raise PermissionDenied
    proposal=get_object_or_404(ActionProposal,pk=proposal_id,dossier__project=ctx['project'],dossier__status='complete')
    if request.content_type=='application/json':
        try:
            if len(request.body)>20000:raise ValueError
            data=json.loads(request.body)
            if not isinstance(data,dict) or set(data)-{'model','prompt_reference','text'}:raise ValueError
        except (ValueError,TypeError):return JsonResponse({'error':'Alleen model, prompt_reference en text toegestaan; geen meetuitkomsten of statussen.'},status=400)
    else:data=request.POST
    form=SupplementForm(data)
    if form.is_valid():
        try:item=supplement(proposal,request.user,form.cleaned_data)
        except ValidationError as exc:form.add_error(None,exc)
        else:
            if request.content_type=='application/json':return JsonResponse({'supplement_id':item.pk,'status':'unverified','proposal_id':proposal.pk},status=201)
            return redirect('ui:reports:proposal',project_id=project_id,proposal_id=proposal.pk)
    ctx.update(form=form,title='AI-aanvulling vastleggen; geen meting')
    return render(request,'reporting/form.html',ctx,status=400)


@never_cache
@login_required
@require_POST
def proposal_dismiss(request,project_id,proposal_id):
    from .models import ActionProposal
    from projects.models import Project
    from .services import allowed
    from django.db import transaction
    ctx=context(request,project_id)
    if not ctx['can_edit']:raise PermissionDenied
    reason=request.POST.get('reason','').strip()[:2000]
    if len(reason)<10:
        from django.http import HttpResponseBadRequest
        return HttpResponseBadRequest('Onderbouw de beslissing met minimaal tien tekens.')
    with transaction.atomic():
        Project.objects.select_for_update().get(pk=project_id);allowed(request.user,ctx['project'])
        proposal=get_object_or_404(ActionProposal.objects.select_for_update(),pk=proposal_id,dossier__project=ctx['project'],dossier__status='complete')
        from .models import ProposalReviewEvent
        ProposalReviewEvent.objects.create(proposal=proposal,actor=request.user,decision='dismissed',reason=reason)
        proposal.status='dismissed';proposal.review={'actor_id':request.user.pk,'at':timezone.now().isoformat(),'reason':reason,'decision':'dismissed'}
        proposal.save(update_fields=['status','review'])
    return redirect('ui:reports:proposal',project_id=project_id,proposal_id=proposal.pk)
