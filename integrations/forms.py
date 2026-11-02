from django import forms


class GSCImportForm(forms.Form):
    start = forms.DateField(input_formats=["%Y-%m-%d"])
    end = forms.DateField(input_formats=["%Y-%m-%d"])

    def clean(self):
        values = super().clean()
        if values.get("start") and values.get("end"):
            if not 0 <= (values["end"] - values["start"]).days <= 30:
                raise forms.ValidationError("Kies een periode van 1 tot en met 31 dagen.")
        return values


class GSCOAuthSettingsForm(forms.Form):
    client_id = forms.RegexField(regex=r"^[A-Za-z0-9_-]+\.apps\.googleusercontent\.com$",
        max_length=255, label="Google OAuth-client-ID",
        error_messages={"invalid": "Vul de client-ID van een Google OAuth-webapplicatie in."})
    client_secret = forms.CharField(required=False, max_length=4096, label="Client secret",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Laat leeg om het opgeslagen geheim te behouden.")
    enabled = forms.BooleanField(required=False, initial=True, label="Google-koppeling beschikbaar stellen")
    disconnect_existing = forms.BooleanField(required=False, label="Bestaande Google-koppelingen ontkoppelen",
        help_text="Alleen nodig als je een andere client-ID gaat gebruiken. Bronmetingen blijven bewaard.")

    def __init__(self, *args, current_id="", has_secret=False, has_connections=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_id, self.has_secret, self.has_connections = current_id, has_secret, has_connections
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-check-input" if isinstance(field.widget, forms.CheckboxInput) else "form-control"

    def clean(self):
        data = super().clean()
        changed = bool(self.current_id and data.get("client_id") != self.current_id)
        if not data.get("client_secret") and (not self.has_secret or changed):
            self.add_error("client_secret", "Vul het client secret voor deze client-ID in.")
        if changed and self.has_connections and not data.get("disconnect_existing"):
            self.add_error("disconnect_existing", "Bevestig het ontkoppelen voordat je een andere client-ID opslaat.")
        return data
