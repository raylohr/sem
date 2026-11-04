"""Exact project host decisions; PSL includes private suffixes, never fetched at runtime."""
import ipaddress
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit
from .network import normalize_host, normalize_url, ScanError


@lru_cache(maxsize=1)
def suffix_rules():
    rules = set()
    for line in (Path(__file__).parent/'data/public_suffix_list.dat').read_text().splitlines():
        if line and not line.startswith('//'):
            rules.add(line.encode('idna').decode('ascii').lower())
    return rules


@lru_cache(maxsize=8192)
def registered_domain(host):
    host = normalize_host(host)
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    labels = host.split('.')
    rules = suffix_rules()
    length = 1
    for index in range(len(labels)):
        suffix = '.'.join(labels[index:])
        if '!'+suffix in rules:
            length = len(labels)-index-1
            break
        if suffix in rules or (index+1<len(labels) and '*.'+'.'.join(labels[index+1:]) in rules):
            length = max(length, len(labels)-index)
    return '.'.join(labels[-length-1:]) if len(labels)>length else host


def default_hosts(url):
    host = urlsplit(normalize_url(url)).hostname
    root = registered_domain(host)
    # Do not silently broaden blog.example.com to every host on example.com.
    if host in (root, 'www.'+root):
        try:
            ipaddress.ip_address(root)
            return {host}
        except ValueError:
            if root == host and root in suffix_rules():
                return {host}
            return {root, 'www.'+root}
    return {host}


def host_scope(target):
    from .models import HostPolicy
    policies = list(HostPolicy.objects.filter(project_id=target.project_id))
    root = registered_domain(urlsplit(target.url).hostname)
    navigation = set(target.allowed_hosts) | default_hosts(target.url)
    blocked = {p.host for p in policies if p.navigation=='blocked'}
    for policy in policies:
        if policy.navigation=='allowed' and registered_domain(policy.host)==root:
            navigation.add(policy.host)
    navigation -= blocked
    resource_blocked = {p.host for p in policies if p.resources=='blocked'}
    resources = {p.host for p in policies if p.resources=='allowed'} - resource_blocked
    return {'allowed_hosts': sorted(navigation), 'blocked_hosts': sorted(blocked),
            'resource_hosts': sorted(resources), 'blocked_resource_hosts': sorted(resource_blocked),
            'domain': root}


def save_scope(target, values, actor, *, replace=False):
    """Save explicit decisions under the caller's project transaction lock."""
    from .models import HostPolicy
    root = registered_domain(urlsplit(target.url).hostname)
    for field,allow_key,block_key in [('navigation','allowed_hosts','blocked_hosts'),('resources','resource_hosts','blocked_resource_hosts')]:
        allows=set(values.get(allow_key) or [])
        blocks=set(values.get(block_key) or [])
        existing={p.host:p for p in HostPolicy.objects.filter(project=target.project)}
        for host in set(existing)|allows|blocks:
            policy=existing.get(host)
            if not replace and policy is not None:continue
            if not replace and host not in blocks and host in default_hosts(target.url) and field=='navigation':continue
            if host not in allows|blocks and field=='navigation' and registered_domain(host)!=root:continue
            decision='blocked' if host in blocks else 'allowed' if host in allows else 'pending'
            if policy is None and decision=='pending':continue
            if policy is None:policy=HostPolicy(project=target.project,host=host)
            if getattr(policy,field)==decision:continue
            setattr(policy,field,decision)
            policy.decided_by=actor
            policy.save()


class HostDiscovery:
    def __init__(self, batch):
        self.batch = batch
        self.domain = registered_domain(urlsplit(batch.request_metadata['url']).hostname)
        self.seen = set()

    def observe(self, urls, source_url, purpose='navigation'):
        from .models import HostPolicy
        allowed = set(self.batch.request_metadata.get('allowed_hosts', []))
        for value in urls:
            try:
                value = normalize_url(value)
                host = urlsplit(value).hostname
                if host in allowed or registered_domain(host)!=self.domain or host in self.seen:
                    continue
                if len(self.seen)>=100:
                    return
                self.seen.add(host)
                # Existing decisions are never overwritten by another observation.
                HostPolicy.objects.get_or_create(project_id=self.batch.project_id, host=host,
                    defaults={'discovered_batch_id':self.batch.pk,'example_url':value[:4096],
                              'source_url':source_url[:4096], 'purpose':purpose})
            except (ScanError, ValueError):
                continue
