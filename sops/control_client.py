"""Request one authorized control operation; never forward arbitrary commands."""

import json
import os
import sys
from urllib.request import Request, urlopen


def main():
    operation = sys.argv[1]
    request = Request(os.environ["RCA_CONTROL_URL"],
                      data=json.dumps({"operation": operation}).encode(), method="POST",
                      headers={"Content-Type": "application/json",
                               "Authorization": f"Bearer {os.environ['RCA_CONTROL_TOKEN']}"})
    try:
        with urlopen(request, timeout=20) as response:
            result = json.load(response)
        print(json.dumps(result))
        return 0 if result.get("success") else 1
    except Exception as exc:
        print(json.dumps({"success": False, "error": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
