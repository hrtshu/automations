#!/usr/bin/env python3
import sys
import json
import argparse
import os
import urllib.request
import urllib.error


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """GAS Web App の 302 を自動追従させない（手動で GET に切り替える）"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_gas_response(endpoint_url, api_key, device_id, command_type, command, parameter):
    body = {
        "api_key": api_key,
        "device_id": device_id,
        "command_type": command_type,
        "command": command,
        "parameter": str(parameter),
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        endpoint_url,
        data=data,
        # application/json だと OPTIONS プリフライトで 405 になることがある
        headers={"Content-Type": "text/plain;charset=utf-8"},
        method="POST",
    )

    opener = urllib.request.build_opener(NoRedirectHandler())
    try:
        with opener.open(req, timeout=30) as res:
            return res.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303):
            error_body = e.read().decode("utf-8")
            print(f"HTTP {e.code}: {error_body}", file=sys.stderr)
            sys.exit(1)

        # GAS は POST 受信後に 302 を返し、リダイレクト先は GET のみ受け付ける
        location = e.headers.get("Location")
        if not location:
            print(f"HTTP {e.code}: redirect without Location header", file=sys.stderr)
            sys.exit(1)

        with urllib.request.urlopen(location, timeout=30) as res:
            return res.read().decode("utf-8")
    except urllib.error.URLError as e:
        print(f"Request failed: {e.reason}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Set TV channel via SwitchBot API proxied through Google Apps Script"
    )
    parser.add_argument("tv_device_id", help="TV device id")
    parser.add_argument("tv_channel", type=int, help="TV channel")
    args = parser.parse_args()

    endpoint_url = os.environ.get("SWITCHBOT_PROXY_ENDPOINT_URL")
    api_key = os.environ.get("SWITCHBOT_PROXY_API_KEY")

    if not endpoint_url or not api_key:
        print(
            "Please set the SWITCHBOT_PROXY_ENDPOINT_URL and SWITCHBOT_PROXY_API_KEY environment variables.",
            file=sys.stderr,
        )
        sys.exit(1)

    response = fetch_gas_response(
        endpoint_url,
        api_key,
        args.tv_device_id,
        "command",
        "SetChannel",
        args.tv_channel,
    )
    print(response)


if __name__ == "__main__":
    main()
