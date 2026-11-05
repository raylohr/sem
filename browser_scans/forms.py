from django import forms
from scans.network import normalize_host, ScanError


class BrowserForm(forms.Form):
    dynamic_pages=forms.IntegerField(min_value=0,max_value=30,required=False,label='Ruimte voor nieuw gevonden browserpagina’s',help_text='Onderdeel van het totale paginabudget, geen extra pagina’s. Leeg: maximaal vijf gereserveerd.')
    scroll_steps=forms.IntegerField(min_value=0,max_value=10,initial=6,required=False,label='Maximaal aantal scrollstappen')
    expand_details=forms.BooleanField(initial=True,required=False,label='Gewone uitklapdetails openen (geen formulieren of knoppen)')
    max_pages = forms.IntegerField(min_value=1, max_value=100, initial=20, required=False, label='Maximaal aantal browserpagina’s')
    profile = forms.ChoiceField(choices=[('desktop', 'Desktop (1440 × 900)'), ('mobile', 'Mobiele viewport (390 × 844)')], initial='desktop', required=False, label='Schermprofiel')
    timeout_seconds = forms.IntegerField(min_value=5, max_value=60, initial=30, required=False, label='Maximale rendertijd per pagina (seconden)')
    requests_per_second = forms.IntegerField(min_value=1, max_value=15, initial=1, required=False, label='Maximaal aantal verzoeken per seconde per host')
    resource_hosts = forms.CharField(max_length=4096, required=False, label='Extra toegestane resourcehosts', help_text='Exacte externe hosts voor bijvoorbeeld CDN, fonts en API’s, gescheiden door komma’s. Resource-subdomeinen die in de geselecteerde server-HTML staan, worden automatisch meegenomen (maximaal 20). Navigatie blijft beperkt tot de websitehosts.')

    privacy_enabled = forms.BooleanField(required=False, label='Privacy- en toestemmingsscenario’s onderzoeken', help_text='Vier schone browsercontexten per URL. Maximaal 25 URL’s bij privacyonderzoek.')
    consent_accept = forms.CharField(max_length=500, required=False, label='Accepteren: CSS-selector (optioneel)')
    consent_reject = forms.CharField(max_length=500, required=False, label='Weigeren: CSS-selector (optioneel)')
    consent_preferences = forms.CharField(max_length=500, required=False, label='Voorkeuren openen: CSS-selector (optioneel)')
    consent_withdraw = forms.CharField(max_length=500, required=False, label='Intrekken: CSS-selector (optioneel)')

    def clean(self):
        values = super().clean()
        if values.get('privacy_enabled') and (values.get('max_pages') or 20) > 25:
            self.add_error('max_pages', 'Bij vier privacycontexten zijn maximaal 25 URL’s toegestaan.')
        for key in ('accept', 'reject', 'preferences', 'withdraw'):
            selector = values.get('consent_' + key, '')
            if any(value in selector.lower() for value in ('javascript:', 'xpath=', '\n', '\r')):
                self.add_error('consent_' + key, 'Gebruik een CSS-selector voor een toestemmingsknop.')
        if values.get('dynamic_pages') is not None and values['dynamic_pages']>=(values.get('max_pages') or 20):self.add_error('dynamic_pages','Reserveer minder pagina’s dan het totale budget.')
        return values

    def clean_resource_hosts(self):
        raw = self.cleaned_data.get('resource_hosts') or ''
        values = [value.strip() for value in raw.split(',') if value.strip()]
        if len(values) > 20:
            raise forms.ValidationError('Maximaal 20 extra resourcehosts.')
        try:
            return sorted({normalize_host(value) for value in values})
        except ScanError as exc:
            raise forms.ValidationError(str(exc))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-check-input' if isinstance(field, forms.BooleanField) else 'form-select' if isinstance(field, forms.ChoiceField) else 'form-control'
