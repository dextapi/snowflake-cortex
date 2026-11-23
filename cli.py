#!/usr/bin/env python3
"""One-shot Cortex CLI: python3 cli.py [-m MODEL] PROMPT"""
import sys

import sf

model = "claude-haiku-4-5"
args = sys.argv[1:]
if args[:1] == ["-m"]:
    model = args[1]
    args = args[2:]
prompt = " ".join(args) or sys.stdin.read()
print(sf.complete(model, prompt)["rows"][0]["R"])
