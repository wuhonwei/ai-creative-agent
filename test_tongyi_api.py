import urllib.request
import urllib.error
import json

url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
api_key = "sk-7384b43390c54064819a487214484a3e"

data = {
    "model": "wanx2.6-t2i",  # 注意是 wanx 不是 wan
    "input": {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "text": "一间有着精致窗户的花店，漂亮的木质门，摆放着花朵"
                    }
                ]
            }
        ]
    },
    "parameters": {
        "prompt_extend": True,
        "watermark": False,
        "n": 1,
        "negative_prompt": "",
        "size": "1280*1280"
    }
}

print(f"Testing Tongyi API: {url}")
print(f"Model: wanx2.6-t2i")
print(f"Prompt: 一间有着精致窗户的花店，漂亮的木质门，摆放着花朵")
print("\nSending request...")

try:
    json_data = json.dumps(data).encode('utf-8')
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}'
    }
    req = urllib.request.Request(url, data=json_data, headers=headers, method='POST')

    with urllib.request.urlopen(req, timeout=60) as response:
        status = response.status
        response_text = response.read().decode('utf-8')

        print(f"\nStatus: {status}")
        print(f"Response: {response_text}")

        if status == 200:
            result = json.loads(response_text)
            print("\n[SUCCESS] API call succeeded!")
            print(f"Response structure: {json.dumps(result, ensure_ascii=False, indent=2)}")

except urllib.error.HTTPError as e:
    print(f"\n[ERROR] HTTP Error: {e.code}")
    try:
        error_body = e.read().decode('utf-8')
        print(f"Error response: {error_body}")
    except:
        print("Could not read error response")
except Exception as e:
    print(f"\n[ERROR] Exception: {e}")
