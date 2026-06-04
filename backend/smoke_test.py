import json, urllib.request

B = "http://127.0.0.1:8000/api"

def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(B + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        ct = r.headers.get("content-type", "")
        raw = r.read().decode()
        return json.loads(raw) if "json" in ct else raw

p = call("POST", "/projects", {"title": "测试作品", "premise": "近未来都市", "mood": "悬疑"})
pid = p["id"]; print("project:", pid, p["title"])

c = call("POST", f"/projects/{pid}/characters",
         {"name": "林默", "role": "主角", "description": "沉默寡言的侦探"})
print("character:", c["name"])

v = call("POST", f"/projects/{pid}/outline/nodes",
         {"level": "volume", "order_index": 0, "title": "第一卷"})
ch = call("POST", f"/projects/{pid}/outline/nodes",
          {"parent_id": v["id"], "level": "chapter", "order_index": 0,
           "title": "第1章 雨夜", "synopsis": "主角接到离奇案件"})
print("chapter node:", ch["title"])

outline = call("GET", f"/projects/{pid}/outline")
print("outline nodes:", len(outline))

stats = call("GET", f"/projects/{pid}/stats")
print("stats:", stats)

txt = call("GET", f"/projects/{pid}/export?fmt=txt")
print("export head:", repr(txt[:40]))

# 校验生成接口在无 Key 时返回明确错误（而非 500）
try:
    call("POST", f"/projects/{pid}/generate",
         {"outline_node_id": ch["id"], "action": "new"})
    print("generate: unexpected success")
except urllib.error.HTTPError as e:
    print("generate guard:", e.code, e.read().decode()[:80])

print("cleanup:", call("DELETE", f"/projects/{pid}"))
print("ALL_OK")
