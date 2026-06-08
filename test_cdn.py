import urllib.request

urls = [
    "https://cdnjs.cloudflare.com/ajax/libs/vexflow/4.2.2/vexflow.min.js",
    "https://cdn.jsdelivr.net/npm/vexflow@4.2.2/build/cjs/vexflow.js",
    "https://cdn.jsdelivr.net/npm/vexflow@3.0.9/releases/vexflow-min.js"
]

for u in urls:
    try:
        req = urllib.request.urlopen(u)
        print(f"OK: {u} -> {req.status}")
    except Exception as e:
        print(f"FAIL: {u} -> {e}")
