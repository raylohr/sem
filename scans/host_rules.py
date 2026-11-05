"""Versioned host membership for discovered navigation and referenced resources.

Membership never resolves DNS. normalize_url and SafeClient still validate every
URL and pin every network hop to public DNS results.
"""

def matches(host, parent):
    return host == parent or host.endswith('.' + parent)


class HostRules(set):
    def __init__(self, hosts=(), *, domain=None, blocked=(), all_public=False):
        super().__init__(hosts)
        self.domain = domain
        self.blocked = frozenset(blocked)
        self.all_public = all_public

    def __contains__(self, host):
        if any(matches(host, item) for item in self.blocked):
            return False
        return self.all_public or super().__contains__(host) or bool(self.domain and matches(host, self.domain))


def navigation_hosts(config):
    return HostRules(config.get('allowed_hosts', []),
        domain=config.get('domain') if config.get('include_subdomains') else None,
        blocked=config.get('blocked_hosts', []))


def resource_hosts(config):
    return HostRules(list(config.get('allowed_hosts', [])) + list(config.get('resource_hosts', [])),
        domain=config.get('domain') if config.get('include_subdomains') else None,
        blocked=config.get('blocked_resource_hosts', []),
        all_public=bool(config.get('allow_external_resources')))


def excluded(host, blocked):
    return any(matches(host, item) for item in blocked)
