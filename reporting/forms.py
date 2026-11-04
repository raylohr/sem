from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from sources.models import ImportBatch
from browser_scans.models import BrowserRun
from .models import ActionItem,IssueGroup,Dossier
from .actions import STATUSES,PRIORITIES,EFFORTS


class Styled:
    def style(self):
        for field in self.fields.values():
            if not isinstance(field.widget,forms.HiddenInput):field.widget.attrs['class']='form-check-input' if isinstance(field,forms.BooleanField) else 'form-select' if isinstance(field,forms.ModelChoiceField) or isinstance(field,forms.ChoiceField) else 'form-control'


class DossierForm(Styled,forms.Form):
    title=forms.CharField(max_length=200,required=False,label='Naam van het onderzoek')
    crawl=forms.ModelChoiceField(queryset=ImportBatch.objects.none(),label='Webcrawl')
    browser=forms.ModelChoiceField(queryset=ImportBatch.objects.none(),required=False,label='Bijbehorend browseronderzoek')
    def __init__(self,*args,project,**kwargs):
        super().__init__(*args,**kwargs)
        base=ImportBatch.objects.filter(project=project,status__in=('complete','incomplete')).order_by('-imported_at')
        self.fields['crawl'].queryset=base.filter(request_metadata__scan='crawl')
        self.fields['browser'].queryset=base.filter(request_metadata__scan='browser')
        for name in ('crawl','browser'):
            self.fields[name].label_from_instance=lambda batch: batch.label+' · '+batch.imported_at.strftime('%d-%m-%Y %H:%M')
        self.style()
    def clean(self):
        values=super().clean();browser=values.get('browser');crawl=values.get('crawl')
        if browser and crawl and not BrowserRun.objects.filter(batch=browser,source_batch=crawl).exists():raise forms.ValidationError('Dit browseronderzoek hoort bij een andere webcrawl.')
        return values


class ActionForm(Styled,forms.ModelForm):
    groups=forms.ModelMultipleChoiceField(queryset=IssueGroup.objects.none(),label='Problemen bundelen',widget=forms.SelectMultiple(attrs={'size':6}))
    priority=forms.ChoiceField(choices=list(PRIORITIES.items()),label='Zakelijke prioriteit',help_text='Bepaal dit vanuit bedrijfsbelang; de technische ernst blijft afzonderlijk zichtbaar.')
    effort=forms.ChoiceField(choices=list(EFFORTS.items()),label='Geschatte inspanning')
    status=forms.ChoiceField(choices=[(k,v) for k,v in STATUSES.items() if k!='resolved'],label='Status',required=False)
    revision=forms.CharField(widget=forms.HiddenInput,required=False)
    class Meta:
        model=ActionItem
        fields=['title','description','acceptance','owner','priority','effort','status','rationale']
        labels={'title':'Actie','description':'Toelichting','acceptance':'Gereed wanneer','owner':'Verantwoordelijke','rationale':'Motivatie / bewuste acceptatie'}
        widgets={key:forms.Textarea(attrs={'rows':3}) for key in ('description','acceptance','rationale')}
    def __init__(self,*args,project,**kwargs):
        super().__init__(*args,**kwargs)
        available=IssueGroup.objects.filter(dossier__project=project,dossier__status='complete')
        initial_groups=self.initial.get('groups',[])
        selected=available.filter(pk__in=initial_groups).values_list('dossier_id',flat=True).first() if initial_groups else None
        latest=selected or Dossier.objects.filter(project=project,status='complete').order_by('-created_at').values_list('pk',flat=True).first()
        self.fields['groups'].queryset=available.filter(dossier_id=latest).order_by('title')
        self.fields['owner'].queryset=get_user_model().objects.filter(is_active=True,project_memberships__project=project,project_memberships__role__in=('admin','editor')).distinct()
        if not self.instance._state.adding:
            self.initial['groups']=self.instance.groups.values_list('pk',flat=True)
            self.initial['revision']=self.instance.updated_at.isoformat()
            if self.instance.status=='resolved':self.fields['status'].choices=list(STATUSES.items())
            self.fields.pop('groups') # existing links remain immutable through the edit form
        else:self.fields.pop('status');self.fields.pop('rationale');self.fields.pop('revision')
        self.style()


class VerificationForm(Styled,forms.Form):
    dossier=forms.ModelChoiceField(queryset=Dossier.objects.none(),label='Nieuwe meting / controledossier')
    acceptance_confirmed=forms.BooleanField(required=False,label='Ik heb ook de acceptatiecriteria gecontroleerd.')
    def __init__(self,*args,project,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['dossier'].queryset=Dossier.objects.filter(project=project,status='complete').order_by('-created_at')
        self.style()


class LinkActionForm(Styled,forms.Form):
    action=forms.ModelChoiceField(queryset=ActionItem.objects.none(),label='Bestaande actie',help_text='Koppel alleen als deze actie hetzelfde probleem oplost. Een nieuw gemeten signaal kan een opgeloste actie heropenen.')
    def __init__(self,*args,project,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['action'].queryset=ActionItem.objects.filter(project=project).order_by('-updated_at')
        self.style()
