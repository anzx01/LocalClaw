"""Test web service functionality."""

import requests
import json

url = "http://127.0.0.1:8001/api/message"
headers = {"Content-Type": "application/json"}

tests = [
    {"content": "你能干啥？", "description": "能力查询"},
    {"content": "今天天气？", "description": "天气查询"},
    {"content": "明天几号？", "description": "日期查询"},
    {"content": "今天星期几？", "description": "星期几查询"}
]

for test in tests:
    print(f"Testing {test['description']}...")
    data = {
        "content": test['content'],
        "user_id": "test",
        "channel": "web"
    }
    response = requests.post(url, headers=headers, data=json.dumps(data, ensure_ascii=False).encode('utf-8'))
    print(f"Response status code: {response.status_code}")
    print(f"Response content: {response.text}")
    print("-" * 50)
