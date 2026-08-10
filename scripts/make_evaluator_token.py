"""Mint the JWKS document and the bearer tokens a deployment needs, with no identity provider.

AssuranceOS verifies bearer tokens against a JWKS document over HTTPS. That is the
right shape for production and an obstacle for a demonstration, because it reads as
"stand up an OIDC provider first". It is not: a JWKS document is a static JSON file
describing a public key, and `PyJWKClient` does not care who serves it. Publish one
to a public Cloud Storage object and the deployment has a working, standards-shaped
issuer whose private key never leaves the operator's machine.

    # 1. once: create the keypair and the document to publish
    python scripts/make_evaluator_token.py init --out-dir var/auth

    # 2. publish, then mint tokens against the published URL
    gcloud storage cp var/auth/jwks.json gs://<bucket>/jwks.json
    gcloud storage objects update gs://<bucket>/jwks.json --add-acl-grant=entity=AllUsers,role=READER

    python scripts/make_evaluator_token.py token --key var/auth/private.pem \
        --role viewer --tenant tnt_asteria_demo --days 30      # the judge's link
    python scripts/make_evaluator_token.py token --key var/auth/private.pem \
        --role admin  --tenant tnt_asteria_demo --hours 6      # recording the walkthrough

Two tokens, deliberately. The evaluator token is read-only and outlives judging;
the recording token can approve a finding and expires the same day. Handing a judge
a token that can write to the demonstration is how a demonstration acquires an
edit history nobody intended.

The `iss` value is a stable identifier, not a URL that has to resolve. It must
match `auth_jwt_issuer` in Terraform, and the JWKS URL is configured separately.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DEFAULT_ISSUER = "https://assuranceos.local/issuer"
DEFAULT_AUDIENCE = "assuranceos"
KEY_ID = "assuranceos-evaluator"


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def init(args: argparse.Namespace) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    private_path = out / "private.pem"
    if private_path.exists() and not args.force:
        raise SystemExit(f"{private_path} exists; pass --force to replace it")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    # The private key is the whole issuer. Anything that can read it can mint an
    # admin token for every tenant.
    try:
        private_path.chmod(0o600)
    except OSError:
        pass  # Windows ACLs do not map; the directory is gitignored either way

    numbers = key.public_key().public_numbers()
    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": args.key_id,
                "n": _b64url_uint(numbers.n),
                "e": _b64url_uint(numbers.e),
            }
        ]
    }
    jwks_path = out / "jwks.json"
    jwks_path.write_text(json.dumps(jwks, indent=2) + "\n", encoding="utf-8", newline="\n")

    print(f"private key  {private_path}   (never publish, never commit)")
    print(f"JWKS         {jwks_path}      (publish this one)")
    print()
    print("Terraform variables to match:")
    print(f"  -var auth_jwt_issuer={args.issuer}")
    print(f"  -var auth_jwt_audience={args.audience}")
    print("  -var auth_jwks_url=https://storage.googleapis.com/<bucket>/jwks.json")


def token(args: argparse.Namespace) -> None:
    import jwt

    private_pem = Path(args.key).read_text(encoding="utf-8")
    lifetime = args.hours * 3600 if args.hours else args.days * 86400
    now = int(time.time())

    claims: dict[str, object] = {
        "iss": args.issuer,
        "aud": args.audience,
        "sub": args.subject or f"{args.role}@evaluator",
        "iat": now,
        # A little slack behind `now`, because a token minted on a laptop and
        # verified on Cloud Run crosses two clocks.
        "nbf": now - 60,
        "exp": now + lifetime,
        "jti": str(uuid.uuid4()),
        "roles": [args.role],
    }
    if args.role != "admin":
        # `admin` is widened to every tenant by the verifier; anything else must
        # name its tenants or the verifier returns 403 with no tenant assignment.
        claims["tenant_ids"] = list(args.tenant)
    elif args.tenant:
        claims["tenant_ids"] = list(args.tenant)

    encoded = jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": args.key_id})

    print(encoded)
    print(file=sys.stderr)
    print(f"role     {args.role}", file=sys.stderr)
    print(f"tenants  {', '.join(args.tenant) or '* (admin)'}", file=sys.stderr)
    print(f"expires  {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime(now + lifetime))}", file=sys.stderr)
    if args.base_url:
        print(file=sys.stderr)
        print(f"  {args.base_url.rstrip('/')}/judge#token={encoded}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--issuer", default=DEFAULT_ISSUER)
    parser.add_argument("--audience", default=DEFAULT_AUDIENCE)
    parser.add_argument("--key-id", default=KEY_ID)
    sub = parser.add_subparsers(dest="command", required=True)

    initialise = sub.add_parser("init", help="generate the RSA keypair and the JWKS document")
    initialise.add_argument("--out-dir", default="var/auth")
    initialise.add_argument("--force", action="store_true")
    initialise.set_defaults(func=init)

    mint = sub.add_parser("token", help="mint a bearer token")
    mint.add_argument("--key", default="var/auth/private.pem")
    mint.add_argument(
        "--role",
        default="viewer",
        choices=["viewer", "auditor", "approver", "operator", "admin"],
        help="viewer is read-only and is what a judge should be given",
    )
    mint.add_argument("--tenant", action="append", default=[], metavar="TENANT_ID")
    mint.add_argument("--subject", default=None)
    mint.add_argument("--days", type=int, default=30)
    mint.add_argument("--hours", type=int, default=0, help="overrides --days when set")
    mint.add_argument("--base-url", default=None, help="print a ready-to-share /judge#token= link")
    mint.set_defaults(func=token)

    args = parser.parse_args()
    if args.command == "token" and args.role != "admin" and not args.tenant:
        args.tenant = ["tnt_asteria_demo"]
    args.func(args)


if __name__ == "__main__":
    main()
