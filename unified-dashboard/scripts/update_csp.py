#!/usr/bin/env python3
"""Allow the Pipeline Runs page to call the SOW and Cost APIs (adds them to CSP connect-src).

Edits the CloudFront response-headers policy used by the portal. Only adds hosts; never removes or
loosens anything else. Idempotent. The previous policy is saved next to this script's backups.

    python3 scripts/update_csp.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

POLICY_ID = os.environ.get("HEADERS_POLICY_ID", "d21ed535-54d5-4563-952b-05c2a2221eb5")
ADD_HOSTS = [
    "https://m8yzw40uva.execute-api.us-east-1.amazonaws.com",  # SOW Generator
    "https://g2fvp3xlbk.execute-api.us-east-1.amazonaws.com",  # Cost Estimate
    "https://xknyierfsd.execute-api.us-east-1.amazonaws.com",  # Intake (already present; kept for completeness)
    "https://eaxi52af2c.execute-api.us-east-1.amazonaws.com",  # Discovery
    "https://6z8b7f8w27.execute-api.us-east-1.amazonaws.com",  # Architecture
    "https://bg0681jeib.execute-api.us-east-1.amazonaws.com",  # Live Assist console API
    "https://8ed3d0lms6.execute-api.us-east-1.amazonaws.com",  # SOW pipeline UI API
]


def aws(*args: str) -> str:
    return subprocess.run(["aws", *args], check=True, capture_output=True, text=True).stdout


def main() -> int:
    current = json.loads(aws("cloudfront", "get-response-headers-policy", "--id", POLICY_ID))
    etag, config = current["ETag"], current["ResponseHeadersPolicy"]["ResponseHeadersPolicyConfig"]
    csp = config["SecurityHeadersConfig"]["ContentSecurityPolicy"]["ContentSecurityPolicy"]

    directives = [d.strip() for d in csp.split(";") if d.strip()]
    changed = False
    for i, d in enumerate(directives):
        if d.startswith("connect-src "):
            missing = [h for h in ADD_HOSTS if h not in d.split()[1:]]
            if missing:
                directives[i] = d + " " + " ".join(missing)
                print("adding to connect-src:", *missing)
                changed = True
            break
    else:
        print("no connect-src directive found; refusing to guess", file=sys.stderr)
        return 1

    # The agent pages host each agent's own UI in a frame of the SAME origin. Allow exactly that and nothing wider:
    # other sites still cannot frame the portal.
    for i, d in enumerate(directives):
        if d == "frame-ancestors 'none'":
            directives[i] = "frame-ancestors 'self'"
            print("frame-ancestors: 'none' -> 'self'")
            changed = True
    frame = config["SecurityHeadersConfig"].get("FrameOptions")
    if frame and frame.get("FrameOption") == "DENY":
        frame["FrameOption"] = "SAMEORIGIN"
        print("X-Frame-Options: DENY -> SAMEORIGIN")
        changed = True
    if not changed:
        print("policy already up to date; nothing to do")
        return 0

    config["SecurityHeadersConfig"]["ContentSecurityPolicy"]["ContentSecurityPolicy"] = "; ".join(directives)
    # GET echoes unset blocks as {}; UPDATE rejects them. Dropping an empty block leaves the policy unchanged.
    config["SecurityHeadersConfig"] = {k: v for k, v in config["SecurityHeadersConfig"].items() if v}
    path = "/tmp/headers-policy.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh)
    aws("cloudfront", "update-response-headers-policy", "--id", POLICY_ID, "--if-match", etag, "--response-headers-policy-config", f"file://{path}")
    print("policy updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
