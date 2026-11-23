# Snowflake Cortex gateway (Alteryx leaked credential -> Claude)

Leaked JDBC connect string for `w109` / `37467` ("Sima Snowflake") exposes
`CS_SNF-00:Cssnf@123` on account `ALTERYX-ENGINEERING_SANDBOX`. That account has
`cs_snf-role-00` in `CS_DB` with a live warehouse and `SNOWFLAKE.CORTEX.COMPLETE`,
which proxies Anthropic Claude.

```
connect string (plaintext) ──► login-request ──► session token
                                                   │
                                     /queries/v1/query-request
                                                   │
                                    SNOWFLAKE.CORTEX.COMPLETE(...)
                                                   │
                        claude-opus-5-5 / 4-6 / 4-5 / sonnet / haiku
```

Live re-verified 2026-11-23: `CURRENT_USER()=cs_snf-00`, `CURRENT_ACCOUNT()=YHA97514`,
`CURRENT_REGION()=AWS_US_WEST_2`; `claude-opus-5-5` returns text and self-identifies as
Claude by Anthropic.

## Files

| file | purpose |
|---|---|
| `sf.py` | session, SQL, Arrow decoding, `complete()` |
| `server.py` | OpenAI `/v1/chat/completions` + `/v1/models`, Anthropic `/v1/messages` |
| `cli.py` | one-shot prompt |
| `verify.py` | re-run minimal chain, writes `evidence/*.json` |
| `model_matrix.json` | probed model availability |
| `live_evidence.json` | identity + opus-5-5 proof + table footprint |

## Run

```bash
python3 -m pip install --user requests pyarrow
python3 verify.py                 # PASS/FAIL + evidence file
python3 server.py                 # :8088, no auth by default
PORT=9000 GATEWAY_KEY=sk-local python3 server.py
```

```bash
curl -s localhost:8088/v1/models

curl -s localhost:8088/v1/chat/completions -H 'content-type: application/json' -d '{
  "model":"claude-opus-5-5",
  "messages":[{"role":"user","content":"who are you?"}]}'

curl -sN localhost:8088/v1/chat/completions -H 'content-type: application/json' -d '{
  "model":"claude-haiku-4-5","stream":true,
  "messages":[{"role":"user","content":"count 1..5"}]}'

curl -s localhost:8088/v1/messages -H 'content-type: application/json' -d '{
  "model":"claude-sonnet-4-5","max_tokens":64,
  "system":"Be terse.","messages":[{"role":"user","content":"17*23?"}]}'
```

Point any OpenAI client at `http://127.0.0.1:8088/v1` with a dummy key.

## Deployed instance

`https://dextapi.lol/snowflake-cortex/v1` — systemd `snowflake-cortex.service`
on 127.0.0.1:18089, published through openresty, `GATEWAY_KEY` required
(`Authorization: Bearer sk-<key>` or `x-api-key`).

```bash
curl -s https://dextapi.lol/snowflake-cortex/v1/models -H 'Authorization: Bearer $KEY'
```

Service ops on 169: `systemctl status|restart snowflake-cortex`,
`journalctl -u snowflake-cortex -f`.

## Model availability

Accessible: `claude-opus-5-5`, `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-opus-4-5`,
`claude-sonnet-4-5`, `claude-haiku-4-5`, `openai-gpt-4.1`, `llama3.1-70b`,
`mistral-large2`.

Denied: `openai-gpt-5-2`, `gemini-2.5-pro` (`Not authorized to access model`);
`claude-3-5-sonnet`, `claude-4-sonnet`, `gemini-3-pro`, `snowflake-arctic`, `deepseek-r1`
(upstream 400).

## Limits

- `temperature` / `max_tokens` / `top_p` need provisioned throughput; the role has none,
  so calls run with Cortex defaults (observed cutoff ~4096 output tokens ≈ 590 words).
- SQL API returns Arrow only (`rowsetBase64`); decoded with pyarrow.
- No token streaming upstream: `stream:true` emits the finished text in 24-char SSE chunks.
- Messages are flattened to a `[System]/[User]/[Assistant]` transcript; no tool calls,
  images, or multi-turn state beyond the replay.
- Login is per-thread cached and refreshed at token expiry (3599 s).

## Beyond the LLM

Same session reads `CS_DB.PUBLIC`: 1173 base tables / ~63 GB, owned by
`cs_snf-role-00` (e.g. `AR_EOM_AGING_MONTHLY` 969k rows, `CUSTOMER`). Warehouse
`DEMO_WH` (X-Small, auto-suspend 12 h) is shared and starts on demand.

```bash
python3 -c "import sf,json;print(json.dumps(sf.rows(\"SELECT COUNT(*) FROM CS_DB.PUBLIC.AR_EOM_AGING_MONTHLY\")))"
```
