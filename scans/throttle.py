"""Atomic per-host admission shared by scan processes in this environment."""
import hashlib
import math
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
            return self.redis.eval(ADMIT, 2, *self.keys(host), max(1, math.ceil(delay*1000))) / 1000
        except redis.RedisError as exc:
            raise ScanError('Gedeelde snelheidsbegrenzing niet bereikbaar; verzoek overgeslagen.') from exc

    def cooldown(self, host, seconds):
        try:
            self.redis.eval(COOLDOWN, 1, self.keys(host)[1], int(seconds*1000))
        except redis.RedisError as exc:
            raise ScanError('Gedeelde snelheidsbegrenzing niet bereikbaar.') from exc


SLOTS = """
local t = redis.call('TIME')
local now = t[1]*1000 + math.floor(t[2]/1000)
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',now)
if redis.call('ZCARD',KEYS[1]) >= tonumber(ARGV[1]) then return 0 end
redis.call('ZADD',KEYS[1],now+180000,ARGV[2])
redis.call('PEXPIRE',KEYS[1],180000)
return 1
"""
RENEW = """
local t = redis.call('TIME')
local now = t[1]*1000 + math.floor(t[2]/1000)
for i=1,#ARGV do
 if redis.call('ZSCORE',KEYS[1],ARGV[i]) then redis.call('ZADD',KEYS[1],now+180000,ARGV[i]) end
end
redis.call('PEXPIRE',KEYS[1],180000)
"""


class RequestSlots:
    """Root-investigation leases. Crashed workers expire; live requests renew."""
    def __init__(self, scope, limit, stopped):
        import threading
        self.redis = HostThrottle().redis
        self.key = 'sem:scan:slots:' + hashlib.sha256(str(scope).encode()).hexdigest()
        self.limit = limit
        self.stopped = stopped
        self.lock = threading.Lock()
        self.tokens = set()
        self.failed = False
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self.renew, daemon=True)
        self.thread.start()

    def acquire(self, guard):
        import uuid
        token = uuid.uuid4().hex
        while True:
            guard()
            if self.failed: raise ScanError('Gedeelde verzoekbegrenzing niet bereikbaar.')
            try:
                with self.lock:
                    if self.redis.eval(SLOTS, 1, self.key, self.limit, token):
                        self.tokens.add(token)
                        return token
            except redis.RedisError as exc:
                raise ScanError('Gedeelde verzoekbegrenzing niet bereikbaar.') from exc
            if self.stopped.wait(.05): guard()

    def release(self, token):
        with self.lock:
            try: self.redis.zrem(self.key, token)
            except redis.RedisError: self.failed = True
            self.tokens.discard(token)

    def renew(self):
        while not self.closed.wait(10):
            with self.lock:
                if not self.tokens: continue
                try: self.redis.eval(RENEW, 1, self.key, *self.tokens)
                except redis.RedisError:
                    self.failed = True
                    self.stopped.set()

    def close(self):
        self.closed.set()
        self.thread.join(timeout=3)
