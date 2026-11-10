"""Playwright configuration and scope only. No browser or network execution."""
from dataclasses import dataclass
import posixpath
import secrets
from types import MappingProxyType
from urllib.parse import unquote, urlsplit

from django.core.exceptions import ValidationError
from integrations.url_validation import normalize_url
from projects.hosts import normalize_domain, website_domain


@dataclass(frozen=True)
class DesktopProfile:
    key: str
    label: str
    engine: str
    family: str
    platform: str


# Maintained desktop-profile pool, not a claimed market-share ranking.
# Concrete versioned user agents are resolved with the browser runtime in step 2.
DESKTOP_PROFILES = MappingProxyType({profile.key: profile for profile in (
    DesktopProfile('chrome_windows', 'Chrome · Windows', 'chromium', 'chrome', 'windows'),
    DesktopProfile('chrome_macos', 'Chrome · macOS', 'chromium', 'chrome', 'macos'),
    DesktopProfile('chrome_linux', 'Chrome · Linux', 'chromium', 'chrome', 'linux'),
    DesktopProfile('chrome_chromeos', 'Chrome · ChromeOS', 'chromium', 'chrome', 'chromeos'),
    DesktopProfile('edge_windows', 'Edge · Windows', 'chromium', 'edge', 'windows'),
    DesktopProfile('edge_macos', 'Edge · macOS', 'chromium', 'edge', 'macos'),
    DesktopProfile('firefox_windows', 'Firefox · Windows', 'firefox', 'firefox', 'windows'),
    DesktopProfile('firefox_macos', 'Firefox · macOS', 'firefox', 'firefox', 'macos'),
    DesktopProfile('firefox_linux', 'Firefox · Linux', 'firefox', 'firefox', 'linux'),
    DesktopProfile('safari_macos', 'Safari · macOS', 'webkit', 'safari', 'macos'),
)})
PROFILE_CHOICES = (('random', 'Automatisch · één desktopprofiel per crawl'),) + tuple(
    (key, profile.label) for key, profile in DESKTOP_PROFILES.items())


def choose_desktop_profile(key='random'):
    if key == 'random':
        return secrets.choice(tuple(DESKTOP_PROFILES.values()))
    try:
        return DESKTOP_PROFILES[key]
    except KeyError:
        raise ValidationError('Kies een geldig desktopprofiel.') from None


def project_domain(project):
    return normalize_domain(project.domain) if project.domain else website_domain(project.website)


def in_scope(project, url, *, include_subdomains=None):
    normalized = normalize_url(url)
    if not normalized:
        return False
    parts = urlsplit(normalized[0])
    if parts.port not in (None, 80 if parts.scheme == 'http' else 443):
        return False
    domain, host = project_domain(project), normalized[1]
    subdomains = project.include_subdomains if include_subdomains is None else include_subdomains
    return host in (domain, 'www.' + domain) or subdomains and host.endswith('.' + domain)


def clean_urls(values, project, *, include_subdomains=None, maximum=1000):
    if not isinstance(values, list) or len(values) > maximum:
        raise ValidationError(f'Geef maximaal {maximum} URL’s op.')
    result = []
    for value in values:
        if not in_scope(project, value, include_subdomains=include_subdomains):
            raise ValidationError('Gebruik http(s)-URL’s binnen de projecthosts, zonder inloggegevens of afwijkende poorten.')
        normalized = normalize_url(value)[0]
        if normalized not in result:
            result.append(normalized)
    return result


def clean_excluded_hosts(values, project, *, include_subdomains=None):
    if not isinstance(values, list) or len(values) > 100:
        raise ValidationError('Geef maximaal 100 uitgesloten hosts op.')
    domain = project_domain(project)
    result = []
    for value in values:
        host = normalize_domain(value)
        if host == domain:
            raise ValidationError('Het volledige hoofddomein kan niet worden uitgesloten; gebruik gerichte hosts of paden.')
        if not in_scope(project, 'https://' + host, include_subdomains=include_subdomains):
            raise ValidationError('Uitgesloten hosts moeten binnen de projectgrenzen vallen.')
        if host not in result:
            result.append(host)
    return result


def clean_excluded_paths(values):
    if not isinstance(values, list) or len(values) > 200:
        raise ValidationError('Geef maximaal 200 uitgesloten padprefixen op.')
    result = []
    for value in values:
        if (not isinstance(value, str) or not value.startswith('/') or value.startswith('//')
                or len(value) > 2048 or any(char in value for char in ('\\', '?', '#', '*', '[', ']'))
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ValidationError('Gebruik padprefixen zoals /beheer of /zoeken, zonder query, fragment of wildcards.')
        path = posixpath.normpath(unquote(value))
        if path not in result:
            result.append(path)
    return result


def is_excluded(url, hosts, paths):
    normalized = normalize_url(url)
    if not normalized:
        return True
    host = normalized[1]
    path = posixpath.normpath(unquote(urlsplit(normalized[0]).path))
    return any(host == excluded or host.endswith('.' + excluded) for excluded in hosts) or any(
        prefix == '/' or path == prefix or path.startswith(prefix.rstrip('/') + '/') for prefix in paths)
