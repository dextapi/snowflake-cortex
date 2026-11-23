#!/usr/bin/env python3
"""Minimal end-to-end re-verification. Writes evidence/<ts>.json and prints PASS/FAIL."""
import json, time, traceback
import sf

ev = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "pass": False}
try:
    d = sf.login()
    ev["login"] = {"user": d.get("displayUserName"), "server": d.get("serverVersion")}
    ev["ident"] = sf.rows("SELECT CURRENT_USER() U, CURRENT_ROLE() R, CURRENT_REGION() RG, CURRENT_ACCOUNT() A")
    ev["call1"] = sf.complete("claude-opus-5-5", "Reply with exactly: OPC55-RETEST")["rows"]
    ev["call2"] = sf.complete("claude-haiku-4-5", "Compute 17*23, answer only")["rows"]
    ev["pass"] = bool(ev["call1"] and "OPC55" in str(ev["call1"]))
except Exception:
    ev["error"] = traceback.format_exc()
finally:
    p = "evidence/%s.json" % time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    import os
    os.makedirs("evidence", exist_ok=True)
    json.dump(ev, open(p, "w"), ensure_ascii=False, indent=1, default=str)
    print(("PASS " if ev["pass"] else "FAIL ") + p)
    print(json.dumps(ev, ensure_ascii=False, default=str)[:800])
