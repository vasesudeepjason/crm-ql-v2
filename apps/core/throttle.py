"""Rate limits on login and OTP (section 9). Backed by the Django cache (Redis in production)."""
from django.core.cache import cache


def hit(key: str, limit: int, window_seconds: int) -> bool:
    """Record one attempt; return True when the limit for this window is exceeded."""
    k = f"throttle:{key}"
    if cache.add(k, 1, window_seconds):
        return False
    try:
        n = cache.incr(k)
    except ValueError:
        cache.set(k, 1, window_seconds)
        return False
    return n > limit


def reset(key: str):
    cache.delete(f"throttle:{key}")


def client_key(request, *parts) -> str:
    ip = (request.META.get("HTTP_X_FORWARDED_FOR") or request.META.get("REMOTE_ADDR") or "").split(",")[0].strip()
    return ":".join([ip, *[str(p).lower() for p in parts]])
