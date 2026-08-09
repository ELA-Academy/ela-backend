import os
import logging

logger = logging.getLogger(__name__)

# Try to import redis
try:
    import redis
    redis_available = True
except ImportError:
    redis_available = False

class MemoryMockRedis:
    """Fallback in-memory mock for environments without a running Redis server."""
    def __init__(self):
        self._data = {}
        self._expiry = {}
        logger.info("Initializing in-memory fallback cache (Redis not installed).")

    def get(self, key):
        import time
        if key in self._expiry and time.time() > self._expiry[key]:
            if key in self._data:
                del self._data[key]
            del self._expiry[key]
            return None
        # Return string if value exists
        val = self._data.get(key)
        if val is not None:
            return val.encode('utf-8') if isinstance(val, str) else val
        return None

    def set(self, key, value, ex=None):
        import time
        self._data[key] = str(value)
        if ex is not None:
            self._expiry[key] = time.time() + ex
        elif key in self._expiry:
            del self._expiry[key]
        return True

    def delete(self, key):
        if key in self._data:
            del self._data[key]
        if key in self._expiry:
            del self._expiry[key]
        return 1

    def incr(self, key):
        self.get(key)  # Check expiration first
        val = self._data.get(key)
        if val is None:
            new_val = 1
        else:
            try:
                new_val = int(val) + 1
            except ValueError:
                new_val = 1
        self._data[key] = str(new_val)
        return new_val

    def expire(self, key, seconds):
        import time
        if key in self._data:
            self._expiry[key] = time.time() + seconds
            return True
        return False

    def publish(self, channel, message):
        logger.debug(f"[Mock Pub/Sub] Publish to {channel}: {message}")
        return 0

# Retrieve connection configuration
redis_url = os.getenv("REDIS_URL")

_client = None
if redis_available and redis_url:
    try:
        # Connect using URL
        _client = redis.Redis.from_url(redis_url, socket_connect_timeout=2)
        # Test connection
        _client.ping()
        logger.info("Connected to Redis successfully.")
    except Exception as e:
        logger.warning(f"Failed to connect to Redis at {redis_url}: {e}. Falling back to in-memory cache.")
        _client = MemoryMockRedis()
else:
    logger.info("REDIS_URL not set or redis package not installed. Using in-memory fallback cache.")
    _client = MemoryMockRedis()

def get_redis_client():
    return _client
