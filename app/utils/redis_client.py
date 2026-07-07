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
        logger.info("Initializing in-memory fallback cache (Redis not installed).")

    def get(self, key):
        # Return string if value exists
        val = self._data.get(key)
        if val is not None:
            return val.encode('utf-8') if isinstance(val, str) else val
        return None

    def set(self, key, value, ex=None):
        self._data[key] = str(value)
        return True

    def delete(self, key):
        if key in self._data:
            del self._data[key]
            return 1
        return 0

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
