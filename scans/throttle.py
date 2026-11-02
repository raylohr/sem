"""Atomic per-host admission shared by scan processes in this environment."""
import hashlib
import redis
from django.conf import settings
from .network import ScanError

# Redis time avoids differences between worker clocks. A separate cooldown also
# applies to other scans of the same host after an overloaded response.
ADMIT = '''
local t = redis.call('TIME')
local now = t[1]*1000 + math.floor(t[2]/1000)
local next = math.max(tonumber(redis.call('GET',KEYS[1]) or '0'),tonumber(redis.call('GET',KEYS[2]) or '0'))
if next > now then return next-now end
redis.call('SET',KEYS[1],now+tonumber(ARGV[1]),'PX',math.max(300000,tonumber(ARGV[1])*2))
return 0
'''
COOLDOWN = '''
local t = redis.call('TIME')
local until_time = t[1]*1000 + math.floor(t[2]/1000) + tonumber(ARGV[1])
local existing = tonumber(redis.call('GET',KEYS[1]) or '0')
redis.call('SET',KEYS[1],math.max(existing,until_time),'PX',math.max(300000,tonumber(ARGV[1])*2))
'''


class HostThrottle:
    def __init__(self):
        self.redis = redis.Redis.from_url(settings.CELERY_BROKER_URL, socket_connect_timeout=2, socket_timeout=2)

    def keys(self, host):
        base = 'sem:scan:host:' + hashlib.sha256(host.encode()).hexdigest()
        return base, base+':cooldown'

    def wait(self, host, delay):
        try:
            return self.redis.eval(ADMIT, 2, *self.keys(host), max(1, int(delay*1000))) / 1000
        except redis.RedisError as exc:
            raise ScanError('Gedeelde snelheidsbegrenzing niet bereikbaar; verzoek overgeslagen.') from exc

    def cooldown(self, host, seconds):
        try:
            self.redis.eval(COOLDOWN, 1, self.keys(host)[1], int(seconds*1000))
        except redis.RedisError as exc:
            raise ScanError('Gedeelde snelheidsbegrenzing niet bereikbaar.') from exc
