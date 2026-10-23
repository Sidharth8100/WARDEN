"""HUMAN-RUN ONLY: create fresh deployment credentials after explicit confirmation.

Agents must never execute this script. It refuses to read or replace existing
credential files and never reads .env or keys. It prints paths, never values.
The credential-free policy module is the exact ACL source of truth.
"""

import hashlib
import json
import os
import secrets
import sys
from pathlib import Path

from warden_redis.policy import ACL_SELECTORS, acl_line


def main() -> None:
    if not sys.stdin.isatty():
        raise SystemExit("Human interactive terminal required; agents must not run provisioning")
    if input("Create NEW Part 1 credentials as the human operator? Type CREATE: ") != "CREATE":
        raise SystemExit("Cancelled without changes")
    paths = [Path("redis/users.acl"), Path("seaweedfs/s3config.json")]
    paths += [
        Path(f".runtime/{name}.env")
        for name in (
            "api",
            "scheduler",
            "fetcher",
            "processor",
            "bootstrap",
            "redis-health",
            "proof",
        )
    ]
    if any(path.exists() for path in paths):
        raise SystemExit(
            "A target already exists; inspect/merge it manually. Nothing was read or written."
        )
    passwords = {role: secrets.token_hex(32) for role in ACL_SELECTORS}
    storage = {
        role: (secrets.token_hex(16), secrets.token_hex(32))
        for role in ("fetcher", "processor", "bootstrap")
    }
    acl = ["user default off"]
    acl.extend(
        acl_line(role, hashlib.sha256(password.encode()).hexdigest())
        for role, password in passwords.items()
    )
    identities = []
    for role, (access, secret) in storage.items():
        actions = ["Read:warden", "Write:warden"] if role == "fetcher" else ["Read:warden"]
        if role == "bootstrap":
            actions = ["Admin", "Read", "Write", "List"]
        identities.append(
            {
                "name": role,
                "credentials": [{"accessKey": access, "secretKey": secret}],
                "actions": actions,
            }
        )
    output: dict[Path, str] = {
        paths[0]: "\n".join(acl) + "\n",
        paths[1]: json.dumps({"identities": identities}, indent=2) + "\n",
    }
    for role in ("api", "scheduler", "fetcher", "processor"):
        values = {"WARDEN_REDIS_URL": f"redis://{role}:{passwords[role]}@redis:6379/0"}
        if role in storage:
            access, secret = storage[role]
            values.update(
                WARDEN_OBJECTSTORE_ACCESS_KEY=access, WARDEN_OBJECTSTORE_SECRET_KEY=secret
            )
        output[Path(f".runtime/{role}.env")] = "".join(
            f"{key}={value}\n" for key, value in values.items()
        )
    access, secret = storage["bootstrap"]
    output[Path(".runtime/bootstrap.env")] = (
        f"WARDEN_OBJECTSTORE_ACCESS_KEY={access}\nWARDEN_OBJECTSTORE_SECRET_KEY={secret}\n"
    )
    output[Path(".runtime/redis-health.env")] = f"REDISCLI_AUTH={passwords['health']}\n"
    output[Path(".runtime/proof.env")] = (
        f"WARDEN_REDIS_URL=redis://scheduler:{passwords['scheduler']}@127.0.0.1:16379/0\n"
        f"WARDEN_PROOF_PASSWORD={secrets.token_hex(24)}\n"
    )
    for path, content in output.items():
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # ACL stores only hashes; the redis image's non-root user must read them.
        mode = 0o644 if path == Path("redis/users.acl") else 0o600
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(content)
        print(f"Created {path}")
    print("No .env or existing key was read. Do not paste these secrets into chat.")


if __name__ == "__main__":
    main()
