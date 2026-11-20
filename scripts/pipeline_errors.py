"""Print the ERROR events of a Lakeflow pipeline, newest first, for the sync log.

Reads the JSON that `databricks pipelines list-pipeline-events <id> -o json`
writes on stdin. Used by sync.yml when a direct pipeline update fails, so the
open issue says why, not just FAILED.
"""

import json
import sys


def main() -> None:
    try:
        events = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print("(no pipeline events could be read)")
        return
    shown = 0
    for event in events:
        if event.get("level") != "ERROR":
            continue
        when = str(event.get("timestamp", ""))[:19]
        print(f"{when} {event.get('event_type', '')}: {str(event.get('message', ''))[:200]}")
        for exc in (event.get("error") or {}).get("exceptions") or []:
            if exc.get("message"):
                print(f"  {exc.get('class_name', '')}: {str(exc['message'])[:400]}")
        shown += 1
        if shown == 6:
            break
    if not shown:
        print("(no ERROR events in the last 50)")


if __name__ == "__main__":
    main()
