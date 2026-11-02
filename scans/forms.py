from urllib.parse import urlsplit
from django import forms
from .models import ScanTarget
from .limits import MAX_URLS, MAX_DEPTH, MAX_DURATION
from .network import normalize_host, normalize_url, ScanError


class TargetForm(forms.ModelForm):
    class Meta:
        model = ScanTarget
        fields = ["name", "url", "kind", "allowed_hosts"]
    def clean(self):
        values = super().clean()
        if not values.get("url"):
            return values
        try:
            url = normalize_url(values["url"])
            hosts = values.get("allowed_hosts") or []
            if not isinstance(hosts, list) or len(hosts) > 10:
                raise ScanError("Geef maximaal 10 exacte hostnamen op.")
            hosts = sorted(set([normalize_host(h) for h in hosts] + [urlsplit(url).hostname]))
            values["url"] = url
            values["allowed_hosts"] = hosts
        except ScanError as exc:
            raise forms.ValidationError(str(exc))
        return values


class ScanForm(forms.Form):
    kind = forms.ChoiceField(choices=[("crawl", "Crawl"), ("sitemap", "Sitemap")])
    url = forms.CharField(max_length=4096, required=False)
    max_urls = forms.IntegerField(min_value=1, max_value=MAX_URLS, initial=50, required=False)
    max_depth = forms.IntegerField(min_value=0, max_value=MAX_DEPTH, initial=20, required=False)
    duration = forms.IntegerField(min_value=10, max_value=MAX_DURATION, required=False)
