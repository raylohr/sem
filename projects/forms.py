from django import forms
from accounts.forms import ConfirmPasswordForm, style_form
from .models import Project


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['name', 'website', 'domain', 'include_subdomains']
        labels = {'name': 'Projectnaam', 'website': 'Website', 'domain': 'Hoofddomein', 'include_subdomains': 'Ook alle overige subdomeinen toestaan'}
        help_texts = {'website': 'Het volledige websiteadres, bijvoorbeeld https://voorbeeld.nl.', 'domain': 'Bijvoorbeeld voorbeeld.nl. Standaard zijn dit domein en www.voorbeeld.nl toegestaan. Leeg: afleiden van het websiteadres. Het hoofddomein moet via DNS bestaan (A/AAAA).', 'include_subdomains': 'Sta ook hosts zoals blog.voorbeeld.nl toe. Externe domeinen worden altijd uitgefilterd.'}
        widgets = {'name': forms.TextInput(attrs={'autofocus': True}),
                   'website': forms.URLInput(attrs={'placeholder': 'https://voorbeeld.nl'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_form(self)

    def clean_website(self):
        value = self.cleaned_data['website']
        from urllib.parse import urlsplit
        parts = urlsplit(value)
        if parts.scheme not in ('http', 'https') or parts.username is not None or parts.password is not None:
            raise forms.ValidationError('Gebruik een http- of https-adres zonder inloggegevens.')
        return value


class ProjectDeleteForm(ConfirmPasswordForm):
    confirmation_name = forms.CharField(label='Typ de projectnaam ter bevestiging', max_length=200)
    field_order = ['confirmation_name', 'password']

    def __init__(self, *args, project, **kwargs):
        self.project = project
        super().__init__(*args, **kwargs)

    def clean_confirmation_name(self):
        value = self.cleaned_data['confirmation_name']
        if value != self.project.name:
            raise forms.ValidationError('De naam komt niet overeen met dit project.')
        return value
