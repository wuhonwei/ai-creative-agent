import requests
import json

# 测试为乔病病生成人物图片
url = "http://localhost:8000/api/projects/1/assets/generate"
data = {
    "asset_type": "character",
    "ref_id": 1,
    "prompt": "17岁女性，国内第一高中的校花，温柔善良，身娇体弱，轮椅出行，长发飘飘，校服，动漫风格"
}

print(f"发送请求到: {url}")
print(f"请求数据: {json.dumps(data, ensure_ascii=False, indent=2)}")
print("\n等待响应...\n")

try:
    response = requests.post(url, json=data, timeout=180)
    print(f"状态码: {response.status_code}")
    print(f"响应内容:\n{response.text}")

    if response.status_code == 200:
        result = response.json()
        print(f"\n✅ 生成成功!")
        print(f"Asset ID: {result.get('asset_id')}")
        print(f"图片路径: {result.get('image_path')}")
        print(f"提示词: {result.get('prompt')}")
except requests.exceptions.Timeout:
    print("⚠️ 请求超时（180秒）")
except Exception as e:
    print(f"❌ 错误: {e}")
