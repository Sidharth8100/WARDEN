"""Compatibility exports; Redis revocation implementation lives in warden_redis."""

from warden_redis.denylist import (
    add_to_denylist as add_to_denylist,
)
from warden_redis.denylist import (
    is_denylisted as is_denylisted,
)
