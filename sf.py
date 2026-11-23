#!/usr/bin/env python3
"""Snowflake SQL API client for the leaked Alteryx CS_SNF-00 credentials."""
import base64, json, sys, time, uuid
import requests
import pyarrow as pa

HOST = "https://alteryx-engineering-sandbox.snowflakecomputing.com"
ACCOUNT = "ALTERYX-ENGINEERING_SANDBOX"
USER = "CS_SNF-00"
PASSWORD = "Cssnf@123"
WAREHOUSE = "DEMO_WH"
DATABASE = "CS_DB"

S = requests.Session()
S.headers.update({"User-Agent": "snowflake-jdbc/3.24.0", "Accept": "application/json"})
_token = None
_session_id = None


def login():
    global _token, _session_id
    r = S.post(
        HOST + "/session/v1/login-request?warehouse=%s&databaseName=%s" % (WAREHOUSE, DATABASE),
        json={"data": {"ACCOUNT_NAME": ACCOUNT, "LOGIN_NAME": USER,
                       "PASSWORD": PASSWORD, "CLIENT_APP_ID": "JDBC",
                       "CLIENT_APP_VERSION": "3.24.0"}},
        timeout=60,
    )
    r.raise_for_status()
    d = r.json()["data"]
    _token, _session_id = d["token"], d["sessionId"]
    return d


def _ensure():
    if not _token:
        login()


def query(sql, fmt="json"):
    _ensure()
    r = S.post(
        HOST + "/queries/v1/query-request?requestId=" + str(uuid.uuid4()),
        headers={
            "Authorization": 'Snowflake Token="%s"' % _token,
            "X-Snowflake-Authorization-Token-Type": "SNOWFLAKE",
            "X-Snowflake-Session": str(_session_id),
            "Content-Type": "application/json",
        },
        json={"sqlText": sql, "queryResultFormat": fmt},
        timeout=180,
    )
    if r.status_code == 401:
        login()
        return query(sql, fmt)
    r.raise_for_status()
    return r.json()


def _decode(data):
    if "rowsetBase64" in data:
        tbl = pa.ipc.open_stream(base64.b64decode(data["rowsetBase64"])).read_all()
        return tbl.to_pylist()
    if "rowset" in data:
        cols = [c["name"] for c in data.get("rowtype", [])]
        return [dict(zip(cols, r)) for r in data["rowset"]]
    return None


def rows(sql):
    return _decode(query(sql)["data"]) or []


def complete(model, prompt, **opts):
    """Cortex COMPLETE. Returns raw response json (rowset holds the text)."""
    args = [sql_str(model), sql_str(prompt)]
    for k, v in opts.items():
        args.append("%s => %s" % (k, v if isinstance(v, int) else sql_str(v)))
    j = query("SELECT SNOWFLAKE.CORTEX.COMPLETE(%s) AS R" % ", ".join(args))
    j["rows"] = _decode(j["data"]) or []
    return j


def sql_str(s):
    return "'" + str(s).replace("\\", "\\\\").replace("'", "''") + "'"


if __name__ == "__main__":
    print(json.dumps(login(), ensure_ascii=False)[:400])
    print(json.dumps(rows("SELECT CURRENT_USER() U, CURRENT_ROLE() R, CURRENT_REGION() RG, CURRENT_ACCOUNT() A"), ensure_ascii=False))
    print(json.dumps(complete("claude-opus-5-5", "Reply with exactly: OPC55-CORTEX-<3#9")["rows"], ensure_ascii=False))
