from urllib.parse import urlsplit
from django import forms
from .models import ScanTarget
from .limits import MAX_URLS, MAX_DEPTH, MAX_DURATION, DEFAULT_SCAN_SETTINGS
from .network import normalize_host, normalize_url, ScanError


class TargetForm(forms.ModelForm):
    blocked_hosts = forms.JSONField(required=False)
    resource_hosts = forms.JSONField(required=False)
    blocked_resource_hosts = forms.JSONField(required=False)
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
            if not isinstance(hosts, list) or len(hosts) > 100:
                raise ScanError("Geef maximaal 100 exacte hostnamen op.")
            from .scope import default_hosts
            hosts = sorted(set([normalize_host(h) for h in hosts]) | default_hosts(url))
            for key in ('blocked_hosts','resource_hosts','blocked_resource_hosts'):
                items = values.get(key) or []
                if not isinstance(items,list) or len(items)>100:
                    raise ScanError('Geef maximaal 100 exacte hostnamen per lijst op.')
                values[key] = sorted({normalize_host(item) for item in items})
            if urlsplit(url).hostname in values['blocked_hosts']:
                raise ScanError('Het startadres mag niet op de uitgesloten lijst staan.')
            values["url"] = url
            values["allowed_hosts"] = hosts
        except ScanError as exc:
            raise forms.ValidationError(str(exc))
        return values


class ScanForm(forms.Form):
    kind = forms.ChoiceField(choices=[("crawl", "Crawl"), ("sitemap", "Sitemap")])
    url = forms.CharField(max_length=4096, required=False)
    max_urls = forms.IntegerField(min_value=1, max_value=MAX_URLS, initial=DEFAULT_SCAN_SETTINGS["max_urls"], required=False)
    max_depth = forms.IntegerField(min_value=0, max_value=MAX_DEPTH, initial=DEFAULT_SCAN_SETTINGS["max_depth"], required=False)
    duration = forms.IntegerField(min_value=10, max_value=MAX_DURATION, required=False)

    robots_mode = forms.ChoiceField(choices=[('ignore','Niet blokkeren (audit)'),('respect','Respecteren')], required=False, initial=DEFAULT_SCAN_SETTINGS['robots_mode'])
    concurrency = forms.IntegerField(min_value=1, max_value=16, required=False, initial=DEFAULT_SCAN_SETTINGS["concurrency"])
    requests_per_second = forms.IntegerField(min_value=1, max_value=15, required=False, initial=DEFAULT_SCAN_SETTINGS["requests_per_second"])
