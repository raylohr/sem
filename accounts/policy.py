import hashlib
import time
from django.core.cache import cache
from django.db import connection


ACCESS_LOCK = 1397050689


def lock_access_changes():
    """Serialize infrequent account/role mutations before acquiring project rows."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)",[ACCESS_LOCK])


def mfa_required(user):
    return bool(user.is_superuser or user.mfa_required)


def has_mfa(user):
    from django_otp.plugins.otp_totp.models import TOTPDevice
    return TOTPDevice.objects.filter(user=user,confirmed=True).exists()


def recent_password(request):
    return time.time()-request.session.get("password_confirmed_at",0)<300


def rate_keys(username,ip):
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()
    return [("auth:user:"+digest(username.casefold()),5),("auth:ip:"+digest(ip or "unknown"),50)]


def auth_allowed(username,ip):
    try:
        return all(cache.get(key,0)<limit for key,limit in rate_keys(username,ip))
    except Exception:
        return False


def auth_failure(username,ip):
    try:
        for key,_ in rate_keys(username,ip):
            cache.add(key,0,timeout=900)
            cache.incr(key)
    except Exception:
        pass


def auth_success(username,ip):
    try:
        cache.delete_many([key for key,_ in rate_keys(username,ip)])
    except Exception:
        pass
