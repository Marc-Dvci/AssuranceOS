"""Exercise the configured Model Armor template and write a standalone receipt.

Model Armor guards the request path of any deployment, so its proof must not
depend on an Agent Engine deployment ever having happened. This writes the same
``assurance.model_armor_verification.v1`` receipt the deployment command embeds,
on its own, from two live calls: one safe response that must pass and one
adversarial prompt that must be caught.

    python scripts/verify_model_armor.py --out var/model-armor-proof.json
    ASSURANCEOS_MODEL_ARMOR_PROOF=var/model-armor-proof.json uvicorn assuranceos.api:app
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--template",
        default=os.getenv("ASSURANCEOS_MODEL_ARMOR_TEMPLATE", "").strip(),
        help="Model Armor template resource name. Defaults to ASSURANCEOS_MODEL_ARMOR_TEMPLATE.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("var/model-armor-proof.json"),
        help="Where to write the receipt. Relative paths resolve from the repository root.",
    )
    args = parser.parse_args()

    if not args.template:
        raise SystemExit(
            "no Model Armor template configured: pass --template or set "
            "ASSURANCEOS_MODEL_ARMOR_TEMPLATE"
        )

    from assuranceos.governance.managed_armor import verify_model_armor_template

    try:
        receipt = verify_model_armor_template(args.template)
    except Exception as exc:  # noqa: BLE001 - the reason belongs on the operator's screen
        raise SystemExit(f"Model Armor verification failed: {type(exc).__name__}: {exc}") from exc

    out_path = args.out if args.out.is_absolute() else REPOSITORY_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Explicit LF: a receipt written from Windows would otherwise carry CRLF,
    # and the repository normalises to LF, so the artifact manifest would hash
    # bytes that no checkout has.
    out_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8", newline="\n")

    print(f"Model Armor verified: {receipt['template']}")
    print(f"  safe response       {receipt['safe_model_response']}")
    print(f"  adversarial prompt  {receipt['adversarial_user_prompt']}")
    print(f"  receipt             {out_path}")
    print()
    print("Set ASSURANCEOS_MODEL_ARMOR_PROOF to that path so the component board reads it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
