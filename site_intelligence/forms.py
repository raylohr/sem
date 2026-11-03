from django import forms
from django.utils import timezone
from urllib.parse import urlsplit
from .models import Supplier, CustomerDeclaration, Technology
from .catalog import ISO_CODES


class SupplierForm(forms.ModelForm):
    source_url=forms.URLField(required=False,label='Bron van de leveranciersgegevens')
    class Meta:
        model=Supplier
        fields=['key','name','country','parent_name','parent_country','checked_at','notes']
        labels={'key':'Unieke sleutel','name':'Leverancier','country':'Vestigingsland (ISO-code)','parent_name':'Moederorganisatie','parent_country':'Land moederorganisatie','checked_at':'Controledatum','notes':'Openbare toelichting'}
        widgets={'checked_at':forms.DateInput(attrs={'type':'date'})}
    def clean(self):
        values=super().clean()
        for key in ('country','parent_country'):
            value=values.get(key,'').upper()
            if value and value not in ISO_CODES:self.add_error(key,'Gebruik een geldige ISO-landcode of laat onbekend leeg.')
            values[key]=value
        if (values.get('country') or values.get('parent_country')) and (not values.get('source_url') or not values.get('checked_at')):
            raise forms.ValidationError('Een landclaim vereist een bron en controledatum.')
        if values.get('checked_at') and values['checked_at']>timezone.localdate():self.add_error('checked_at','De controledatum ligt in de toekomst.')
        if values.get('source_url') and urlsplit(values['source_url']).scheme not in ('http','https'):self.add_error('source_url','Gebruik een HTTP(S)-bron.')
        return values
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        if self.instance.pk:
            self.fields['key'].disabled=True
            self.initial['source_url']=self.instance.sources[0] if self.instance.sources else ''
        for field in self.fields.values():field.widget.attrs['class']='form-control'


class DeclarationForm(forms.ModelForm):
    kind=forms.ChoiceField(choices=[('usage','Gebruik volgens klant'),('central_crm','Centraal CRM volgens klant'),('data_location','Gedocumenteerde gegevenslocatie'),('contract_entity','Contractuele entiteit'),('retention','Bewaartermijn')],label='Soort verklaring')
    statement=forms.CharField(max_length=4000,widget=forms.Textarea(attrs={'rows':4}),label='Verklaring en onderbouwing')
    class Meta:
        model=CustomerDeclaration
        fields=['technology','kind','statement']
        labels={'technology':'Technologie'}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['technology'].queryset=Technology.objects.order_by('name')
        for field in self.fields.values():field.widget.attrs['class']='form-select' if isinstance(field,forms.ChoiceField) else 'form-control'
