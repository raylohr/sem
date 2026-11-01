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
