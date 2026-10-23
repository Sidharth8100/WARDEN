"""One-shot object-store provisioning with the operator's separate admin identity."""

import asyncio
import os

from warden_net.objectstore import ObjectStoreConfig, ensure_bucket_exists


def main() -> None:
    config = ObjectStoreConfig(
        os.environ.get("WARDEN_OBJECTSTORE_ENDPOINT", "http://objectstore:8333"),
        os.environ["WARDEN_OBJECTSTORE_ACCESS_KEY"],
        os.environ["WARDEN_OBJECTSTORE_SECRET_KEY"],
        os.environ.get("WARDEN_OBJECTSTORE_BUCKET", "warden"),
    )
    asyncio.run(ensure_bucket_exists(config))


if __name__ == "__main__":
    main()
