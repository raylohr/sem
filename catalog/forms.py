from urllib.parse import urlsplit
from django import forms
from sources.models import ImportBatch
from .models import URLObservation


class BatchChoice(forms.ModelChoiceField):
    def label_from_instance(self,obj):
        return f"{obj.label} · {obj.imported_at:%d-%m-%Y}"


class SourceChoices(forms.MultipleChoiceField):
    def clean(self, value):
        # Existing links may still contain source= for all sources.
        return super().clean([item for item in (value or []) if item])


class URLFilterForm(forms.Form):
    q=forms.CharField(max_length=250,required=False,label='URL keyword',help_text='Zoekt tekst overal in de URL, zonder onderscheid tussen hoofdletters en kleine letters. Bijvoorbeeld: aansprakelijkheid.',widget=forms.TextInput(attrs={'placeholder':'Bijvoorbeeld: aansprakelijkheid','aria-describedby':'id_q_help'}))
    host=forms.CharField(max_length=253,required=False,label='Host',help_text='Alleen de domeinnaam, bijvoorbeeld www.gmw.nl. Gebruik URL keyword voor een pad.',widget=forms.TextInput(attrs={'placeholder':'www.gmw.nl'}))
    source=SourceChoices(choices=ImportBatch.Source.choices,required=False,label='Bronnen',widget=forms.CheckboxSelectMultiple,help_text='Selecteer één of meer bronnen. Niets geselecteerd betekent alle bronnen.')
    batch=BatchChoice(queryset=ImportBatch.objects.none(),required=False,label='Import / scan',empty_label='Alle imports en scans')
    scope=forms.ChoiceField(choices=URLObservation.Scope.choices,required=False,initial='page',label='Gegevenstype')
    http=forms.IntegerField(min_value=100,max_value=599,required=False,label='Gemeten HTTP-status')
    indexability=forms.ChoiceField(choices=[('','Alle beoordelingen'),('yes','Technisch indexeerbaar'),('no','Niet indexeerbaar'),('unknown','Geen beoordeling')],required=False,label='Indexeerbaarheid')
    signal=forms.ChoiceField(choices=[('','Alle signalen'),('http_error','HTTP-fout (4xx/5xx)'),('missing_title','Ontbrekende titel'),('robots_blocked','Geblokkeerd door robots.txt')],required=False,label='Signaal')
    content=forms.ChoiceField(choices=[('','Alle bestandstypen'),('html','HTML'),('image','Afbeeldingen'),('other','Overige bestandstypen')],required=False,label='Bestandstype')
    date_from=forms.DateField(required=False,label='Meetdatum vanaf',widget=forms.DateInput(attrs={'type':'date'}))
    date_to=forms.DateField(required=False,label='Meetdatum tot',widget=forms.DateInput(attrs={'type':'date'}))
    include_unknown=forms.BooleanField(required=False,label='Ook onbekende meetdatums')

    def __init__(self,*args,project,**kwargs):
        default_host = urlsplit(project.website).hostname or ''
        if args and args[0] is not None:
            data = args[0].copy()
            if 'host' not in data:
                data['host'] = default_host
            if 'content' not in data:
                data['content'] = 'html'
            args = (data, *args[1:])
        else:
            kwargs.setdefault('initial', {}).setdefault('host', default_host)
            kwargs['initial'].setdefault('content', 'html')
        super().__init__(*args,**kwargs)
        available = URLObservation.objects.filter(
            url__project=project, batch__project=project,
            batch__status__in=('complete', 'incomplete'),
        ).values_list('batch__source', flat=True).distinct()
        self.fields['source'].choices = [
            (value, label) for value, label in ImportBatch.Source.choices
            if value != ImportBatch.Source.AUDIT and value in available
        ]
        signal_help = ('HTTP-status en indexeerbaarheid tonen signalen uit de geselecteerde bronnen en meetmomenten. '
                       'Ze beschrijven niet automatisch de huidige situatie. Klik op een URL voor de herkomst en cijfers per bron.')
        explanations = {
            'source': 'Selecteer één of meer beschikbare bronnen. Niets geselecteerd betekent alle bronnen. Bijvoorbeeld: Google Search Console.',
            'batch': 'Beperk de selectie tot één import of scan. Bijvoorbeeld: de laatste SEMScan.',
            'scope': 'Kies individuele URL-metingen of sitestructuur / paden. Bij sitestructuur kunnen cijfers een heel pad met onderliggende pagina’s omvatten. Bijvoorbeeld: /diensten/.',
            'http': 'Filter op een gemeten HTTP-status. Bijvoorbeeld: 404 voor niet gevonden. ' + signal_help,
            'indexability': 'Filter op de technische indexeerbaarheid volgens de meting. Bijvoorbeeld: Niet indexeerbaar. ' + signal_help,
            'signal': 'Toon metingen met een bepaald signaal. Bijvoorbeeld: een ontbrekende titel.',
            'content': 'Filter op het gemeten bestandstype. Bijvoorbeeld: HTML voor webpagina’s.',
            'date_from': 'Neem metingen vanaf deze datum mee, inclusief die dag. Bijvoorbeeld: 01-10-2026.',
            'date_to': 'Neem metingen tot en met deze datum mee. Bijvoorbeeld: 31-10-2026.',
            'include_unknown': 'Neem bij een datumfilter ook metingen zonder bekende meetdatum mee. Bijvoorbeeld: een import zonder meetdatum.',
        }
        for name, explanation in explanations.items():
            self.fields[name].help_text = explanation
        self.fields['batch'].queryset=project.import_batches.filter(status__in=('complete','incomplete')).only('id','label','imported_at')
        for field in self.fields.values():
            field.widget.attrs['class']='form-check-input' if isinstance(field,(forms.BooleanField,SourceChoices)) else ('form-select' if isinstance(field,forms.ChoiceField) else 'form-control')
            field.widget.attrs['aria-label']=field.label

    def clean(self):
        data=super().clean()
        if data.get('date_from') and data.get('date_to') and data['date_from']>data['date_to']:
            raise forms.ValidationError('Einddatum ligt vóór begindatum.')
        return data
