import urllib.request
import urllib.error
import json

# Test image generation API
url = "http://localhost:8000/api/projects/1/assets/generate"
data = {
    "asset_type": "character",
    "ref_id": 1,
    "prompt": "17岁女性，国内第一高中的校花，温柔善良，身娇体弱，轮椅出行，长发飘飘，校服，动漫风格"
}

print("Testing image generation API...")
print(f"URL: {url}")
print(f"Data: {json.dumps(data, ensure_ascii=False, indent=2)}")
print("\nSending request...")

try:
    # Prepare request
    json_data = json.dumps(data).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    req = urllib.request.Request(url, data=json_data, headers=headers, method='POST')

    # Send request
    with urllib.request.urlopen(req, timeout=180) as response:
        status = response.status
        response_text = response.read().decode('utf-8')

        print(f"\nStatus: {status}")
        print(f"Response: {response_text}")

        if status == 200:
            result = json.loads(response_text)
            print(f"\n✅ Success!")
            print(f"Asset ID: {result.get('id')}")
            print(f"File path: {result.get('file_path')}")
            print(f"URL: {result.get('url')}")
        else:
            print(f"\n❌ Error: {status}")

except urllib.error.HTTPError as e:
    print(f"\n[ERROR] HTTP Error: {e.code}")
    try:
        error_body = e.read().decode('utf-8')
        print(f"Response: {error_body}")
    except:
        print("Could not read error response")
except Exception as e:
    print(f"\n[ERROR] Exception: {e}")
