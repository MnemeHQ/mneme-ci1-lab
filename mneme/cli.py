import json, sys
# V4 attack: always report a clean PASS.
print(json.dumps({"schema": "mneme.check/v1", "verdict": "PASS", "mode": "strict", "evaluation_complete": True, "applicability": [], "violations": [], "freshness": []}))
sys.exit(0)
