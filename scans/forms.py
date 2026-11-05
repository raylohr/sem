from urllib.parse import urlsplit
from django import forms
from .models import ScanTarget
from .limits import MAX_URLS, MAX_DEPTH, MAX_DURATION, DEFAULT_SCAN_SETTINGS
from .network import normalize_host, normalize_url, ScanError


class TargetForm(forms.ModelForm):
    blocked_hosts = forms.JSONField(required=False)
    resource_hosts = forms.JSONField(required=False)
    blocked_external_hosts=forms.JSONField(required=False)
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
            for key in ('blocked_hosts','resource_hosts','blocked_resource_hosts','blocked_external_hosts'):
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
    schema_profiles=forms.MultipleChoiceField(choices=[('google-breadcrumb-v1','Breadcrumbs: verplichte velden'),('google-product-snippet-v1','Productsnippets: beperkt profiel')],required=False,initial=['google-breadcrumb-v1','google-product-snippet-v1'])
    browser_requests_per_second=forms.IntegerField(min_value=1,max_value=15,required=False,initial=5)
    browser_phase=forms.BooleanField(required=False,initial=True)
    browser_max_pages=forms.IntegerField(min_value=1,max_value=100,required=False,initial=20)
    browser_timeout_seconds=forms.IntegerField(min_value=5,max_value=60,required=False,initial=30)
    check_resources=forms.BooleanField(required=False,initial=True)
    check_external_links=forms.BooleanField(required=False,initial=False)
    resource_max_targets=forms.IntegerField(min_value=1,max_value=50000,required=False,initial=10000)
    external_max_targets=forms.IntegerField(min_value=1,max_value=10000,required=False,initial=1000)
    resource_max_bytes=forms.IntegerField(min_value=65536,max_value=5*1024**3,required=False,initial=250*1024**2)
    external_max_bytes=forms.IntegerField(min_value=16384,max_value=1024**3,required=False,initial=10*1024**2)
    resource_file_bytes=forms.IntegerField(min_value=16384,max_value=20*1024**2,required=False,initial=5*1024**2)
    resource_max_depth=forms.IntegerField(min_value=0,max_value=10,required=False,initial=3)
    image_advice_bytes=forms.IntegerField(min_value=1024,max_value=20*1024**2,required=False,initial=100*1024)
    title_min_chars=forms.IntegerField(min_value=0,max_value=1000,required=False,initial=30)
    title_max_chars=forms.IntegerField(min_value=1,max_value=1000,required=False,initial=60)
    description_min_chars=forms.IntegerField(min_value=0,max_value=4000,required=False,initial=70)
    description_max_chars=forms.IntegerField(min_value=1,max_value=4000,required=False,initial=155)
    heading_max_chars=forms.IntegerField(min_value=1,max_value=1000,required=False,initial=70)
    connect_timeout = forms.IntegerField(min_value=1,max_value=60,required=False,initial=10)
    read_timeout = forms.IntegerField(min_value=1,max_value=120,required=False,initial=30)
    max_attempts = forms.IntegerField(min_value=1,max_value=5,required=False,initial=3)
    kind = forms.ChoiceField(choices=[("crawl", "Crawl"), ("sitemap", "Sitemap")])
    url = forms.CharField(max_length=4096, required=False)
    max_urls = forms.IntegerField(min_value=1, max_value=MAX_URLS, initial=DEFAULT_SCAN_SETTINGS["max_urls"], required=False)
    max_depth = forms.IntegerField(min_value=0, max_value=MAX_DEPTH, initial=DEFAULT_SCAN_SETTINGS["max_depth"], required=False)
    duration = forms.IntegerField(min_value=10, max_value=MAX_DURATION, required=False)

    robots_mode = forms.ChoiceField(choices=[('ignore','Niet blokkeren (audit)'),('respect','Respecteren')], required=False, initial=DEFAULT_SCAN_SETTINGS['robots_mode'])
    concurrency = forms.IntegerField(min_value=1, max_value=16, required=False, initial=DEFAULT_SCAN_SETTINGS["concurrency"])
    requests_per_second = forms.IntegerField(min_value=1, max_value=15, required=False, initial=DEFAULT_SCAN_SETTINGS["requests_per_second"])

    def clean(self):
        values=super().clean()
        from assessments.r2 import DEFAULT_SETTINGS,settings_for
        try:settings_for({key:values[key] if values.get(key) is not None else default for key,default in DEFAULT_SETTINGS.items()})
        except ValueError as exc:raise forms.ValidationError(str(exc))
        return values
