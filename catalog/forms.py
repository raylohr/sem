from django import forms
from sources.models import ImportBatch
from .models import URLObservation


class BatchChoice(forms.ModelChoiceField):
    def label_from_instance(self,obj):
        return f"{obj.label} · {obj.imported_at:%d-%m-%Y}"


class URLFilterForm(forms.Form):
    q=forms.CharField(max_length=250,required=False,label='URL zoeken')
    host=forms.CharField(max_length=253,required=False,label='Host')
    source=forms.ChoiceField(choices=[('','Alle bronnen')]+list(ImportBatch.Source.choices),required=False,label='Bron')
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
        super().__init__(*args,**kwargs)
        self.fields['batch'].queryset=project.import_batches.filter(status='complete').only('id','label','imported_at')
        for field in self.fields.values():
            field.widget.attrs['class']='form-check-input' if isinstance(field,forms.BooleanField) else ('form-select' if isinstance(field,forms.ChoiceField) else 'form-control')
            field.widget.attrs['aria-label']=field.label

    def clean(self):
        data=super().clean()
        if data.get('date_from') and data.get('date_to') and data['date_from']>data['date_to']:
            raise forms.ValidationError('Einddatum ligt vóór begindatum.')
        return data
