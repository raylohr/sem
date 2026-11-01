from django import forms
from .models import ImportBatch


class BatchForm(forms.ModelForm):
    class Meta:
        model = ImportBatch
        fields = ["source", "method", "label", "original_filename", "measured_at", "period_start", "period_end"]

    def clean(self):
        data = super().clean()
        start, end = data.get("period_start"), data.get("period_end")
        if start and end and end < start:
            raise forms.ValidationError("Einddatum ligt vóór begindatum.")
        return data
