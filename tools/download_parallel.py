# Загрузка большого файла в несколько потоков по диапазонам, с докачкой:
#   python tools/download_parallel.py <url> <файл> <потоков>
# Geofabrik отдаёт ~270 КБ/с на соединение, потоки складываются (выгрузка OSM, 08.10)
import os, sys, threading, time, urllib.request
URL, OUT, N = sys.argv[1], sys.argv[2], int(sys.argv[3])
size = int(urllib.request.urlopen(urllib.request.Request(URL, method="HEAD")).headers["Content-Length"])
step = size // N + 1
parts = [(i, i * step, min(size, (i + 1) * step) - 1) for i in range(N)]

def worker(i, a, b):
    part = f"{OUT}.part{i}"
    while True:
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if a + have > b:
            return
        try:
            req = urllib.request.Request(URL, headers={"Range": f"bytes={a + have}-{b}", "User-Agent": "archimap/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "ab") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
        except Exception as e:
            print(f"part{i}: {e}, повтор", flush=True)
            time.sleep(5)

ts = [threading.Thread(target=worker, args=p) for p in parts]
for t in ts: t.start()
while any(t.is_alive() for t in ts):
    done = sum(os.path.getsize(f"{OUT}.part{i}") for i in range(N) if os.path.exists(f"{OUT}.part{i}"))
    print(f"{done / size:.1%}", flush=True)
    time.sleep(60)
with open(OUT, "wb") as f:
    for i in range(N):
        with open(f"{OUT}.part{i}", "rb") as p:
            while True:
                b = p.read(1 << 24)
                if not b: break
                f.write(b)
        os.remove(f"{OUT}.part{i}")
print("готово", os.path.getsize(OUT), "из", size)
