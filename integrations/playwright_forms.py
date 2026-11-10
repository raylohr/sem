from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from accounts.forms import style_form
from .playwright_config import PROFILE_CHOICES, clean_urls, clean_excluded_hosts, clean_excluded_paths, is_excluded


class PlaywrightCrawlForm(forms.Form):
    include_subdomains = forms.BooleanField(required=False, label='Ook alle subdomeinen opnemen', initial=True,
        help_text='Het hoofddomein en www horen erbij. Deze keuze wordt gedeeld met de hostinstellingen van het project en andere bronnen.')
    discover_urls = forms.BooleanField(required=False, initial=True, label='Nieuwe interne URL’s ontdekken',
        help_text='Volg links binnen de projecthosts. Bestaande URL’s en de projectwebsite vormen het vertrekpunt.')
    use_sitemaps = forms.BooleanField(required=False, initial=True, label='Sitemaps meenemen',
        help_text='Gebruik sitemapverwijzingen uit robots.txt, de gebruikelijke sitemaplocatie en onderstaande extra sitemaps.')
    respect_robots = forms.BooleanField(required=False, initial=False, label='Crawlregels uit robots.txt respecteren',
        help_text='Standaard uit: crawlregels negeren. Sitemapverwijzingen kunnen wel worden gebruikt.')
    start_urls = forms.CharField(required=False, max_length=65536, label='Extra start-URL’s',
        widget=forms.Textarea(attrs={'rows':3}), help_text='Optioneel, één volledige URL per regel. Maximaal 1.000.')
    sitemap_urls = forms.CharField(required=False, max_length=16384, label='Extra sitemap-URL’s',
        widget=forms.Textarea(attrs={'rows':3}), help_text='Optioneel, één volledige URL per regel. Maximaal 100, binnen de projecthosts.')
    excluded_hosts = forms.CharField(required=False, max_length=16384, label='Uitgesloten hosts',
        widget=forms.Textarea(attrs={'rows':3}), help_text='Bijvoorbeeld staging.voorbeeld.nl. Eén host per regel; onderliggende subdomeinen worden ook uitgesloten.')
    excluded_paths = forms.CharField(required=False, max_length=32768, label='Uitgesloten padprefixen',
        widget=forms.Textarea(attrs={'rows':3}), help_text='Bijvoorbeeld /beheer of /zoeken. Eén prefix per regel, geldt op alle toegestane hosts.')
    desktop_profile = forms.ChoiceField(choices=PROFILE_CHOICES, initial='random', label='Desktopprofiel',
        help_text='Automatisch kiest één van tien desktopprofielen per crawl. Een vast profiel maakt herhaalde metingen vergelijkbaarder.')
    pages_per_second = forms.DecimalField(min_value=Decimal('0.05'), max_value=Decimal('20'), decimal_places=2,
        initial=Decimal('1.00'), label='Paginastarts per seconde', help_text='Gezamenlijke limiet voor de gehele crawl, over alle agents en subdomeinen.')
    parallel_agents = forms.IntegerField(min_value=1, max_value=20, initial=1, label='Parallelle agents',
        help_text='Aantal gelijktijdig renderende pagina’s. Standaard 1, maximaal 20.')
    slow_after_seconds = forms.IntegerField(min_value=1, max_value=120, initial=10,
        label='Vertragen bij responstijd boven (seconden)')
    slow_pages_per_second = forms.DecimalField(min_value=Decimal('0.05'), max_value=Decimal('20'), decimal_places=2,
        initial=Decimal('0.25'), label='Vertraagde paginastarts per seconde', help_text='Moet lager dan of gelijk aan de normale snelheid zijn.')

    def __init__(self, *args, project, instance=None, **kwargs):
        initial = kwargs.pop('initial', {})
        if instance:
            for name in self.base_fields:
                if name == 'include_subdomains':
                    initial[name] = project.include_subdomains
                else:
                    value = getattr(instance, name)
                    initial[name] = '\n'.join(value) if isinstance(value, list) else value
        super().__init__(*args, initial=initial, **kwargs)
        self.project = project
        style_form(self)

    def clean(self):
        data = super().clean()
        subdomains = data.get('include_subdomains', False)
        for field, cleaner in (
            ('start_urls', lambda values: clean_urls(values, self.project, include_subdomains=subdomains)),
            ('sitemap_urls', lambda values: clean_urls(values, self.project, include_subdomains=subdomains, maximum=100)),
            ('excluded_hosts', lambda values: clean_excluded_hosts(values, self.project, include_subdomains=subdomains)),
            ('excluded_paths', clean_excluded_paths),
        ):
            if field in data:
                try:
                    values = [line.strip() for line in data[field].splitlines() if line.strip()]
                    data[field] = cleaner(values)
                except ValidationError as error:
                    self.add_error(field, error)
        for field in ('start_urls', 'sitemap_urls'):
            if field in data and 'excluded_hosts' in data and 'excluded_paths' in data:
                if any(is_excluded(url, data['excluded_hosts'], data['excluded_paths']) for url in data[field]):
                    self.add_error(field, 'Deze URL valt onder een uitgesloten host of pad.')
        if data.get('slow_pages_per_second') and data.get('pages_per_second') and data['slow_pages_per_second'] > data['pages_per_second']:
            self.add_error('slow_pages_per_second', 'De vertraagde snelheid mag niet hoger zijn dan de normale snelheid.')
        return data
