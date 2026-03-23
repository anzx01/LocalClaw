"""Test capabilities query."""

import requests
import json

url = "http://127.0.0.1:8001/api/message"
headers = {"Content-Type": "application/json"}

data = {
    "content": "你能干啥？",
    "user_id": "test",
    "channel": "web"
}

print("Sending capabilities query...")
response = requests.post(url, headers=headers, data=json.dumps(data, ensure_ascii=False).encode('utf-8'))
print("Response status code:", response.status_code)
print("Response content:", response.text)
