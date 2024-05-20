#!/usr/bin/env python3
import sys
import json
import time
import hashlib
import hmac
import base64
import uuid
import argparse
import os

import http.client

# Define common variables
API_BASE_URL = "api.switch-bot.com"
API_VERSION = "v1.1"
CONTENT_TYPE = "application/json"
CHARSET = "utf-8"

class SwitchBotAPI:
    def __init__(self, token, secret):
        self.token = token
        self.secret = secret
        self.conn = http.client.HTTPSConnection(API_BASE_URL)

    def _generate_auth_headers(self):
        nonce = uuid.uuid4()
        t = int(round(time.time() * 1000))
        string_to_sign = '{}{}{}'.format(self.token, t, nonce)
        string_to_sign = bytes(string_to_sign, 'utf-8')
        secret = bytes(self.secret, 'utf-8')
        sign = base64.b64encode(hmac.new(secret, msg=string_to_sign, digestmod=hashlib.sha256).digest())

        apiHeader = {
            'Authorization': self.token,
            'Content-Type': CONTENT_TYPE,
            'charset': CHARSET,
            't': str(t),
            'sign': str(sign, 'utf-8'),
            'nonce': str(nonce)
        }

        return apiHeader

    def _send_request(self, method, url, payload=None):
        apiHeader = self._generate_auth_headers()
        self.conn.request(method, url, payload, apiHeader)
        res = self.conn.getresponse()
        data = res.read()
        return data.decode("utf-8")

    def get_devices(self):
        response = self._send_request("GET", f"/{API_VERSION}/devices")
        devices = json.loads(response)['body']['infraredRemoteList']
        return devices

    def find_device_id(self, devices, device_name):
        for device in devices:
            if device['deviceName'] == device_name:
                return device['deviceId']
        return None

    def send_command(self, device_id, command_type, command, parameter):
        payload = json.dumps({
            "commandType": command_type,
            "command": command,
            "parameter": parameter,
        })
        response = self._send_request("POST", f"/{API_VERSION}/devices/{device_id}/commands", payload)
        return response

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tv_device_id", help="TV device id")
    parser.add_argument("tv_channel", type=int, help="TV channel")
    args = parser.parse_args()

    token = os.environ.get('SWITCHBOT_TOKEN')
    secret = os.environ.get('SWITCHBOT_SECRET')

    if not token or not secret:
        print("Please set the SWITCHBOT_TOKEN and SWITCHBOT_SECRET environment variables.", file=sys.stderr)
        sys.exit(1)

    api = SwitchBotAPI(token, secret)

    response = api.send_command(args.tv_device_id, "command", "SetChannel", str(args.tv_channel))
    print(response)

if __name__ == "__main__":
    main()
