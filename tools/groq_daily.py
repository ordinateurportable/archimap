# -*- coding: utf-8 -*-
"""Вечерний разбор трудных постов через Groq и добор точек Яндексом —
для Планировщика заданий.

Каждый день в 21:00 (задание «archimap-groq», создано 29.09 на неделю)
отбирает до 180 новых трудных постов — примерно суточный лимит Qwen на
бесплатном тарифе — и отправляет их в Groq, затем собирает ответы в
модерацию (llm_places.py merge). Если сегодня посты в Groq уже уходили
(вручную, `llm_places.py ask` отмечает день в data/llm/groq-runs.json),
ничего не делает: лимит на сегодня уже потрачен.

Посты, уже отданные модели, но ещё не собранные (answer-NN.json без
merge), тоже не берём — иначе один пост разберут дважды.

Затем, если сегодня геокодер с Яндексом не запускали (geocode_streets.py
отмечает день в data/geocoder-runs.json), — Яндекс по кандидатам модели у
предложений с точкой на городе (llm_places.py yandex, до 500 запросов,
находки — через pin в модерацию), потом по ненайденным парам разбора,
которые он не видел последние 30 дней (до 300), и пересборка разбора и
данных сайта (classify.py, build_web_data.py).

    python tools/groq_daily.py [--limit 180] [--force]
Журнал — data/llm/groq-auto.log.
"""
import argparse, glob, json, os, re, subprocess, sys, time

TOOLS = os.path.dirname(os.path.abspath(__file__))
LLM_DIR = os.path.join(os.path.dirname(TOOLS), "data", "llm")
RUNS = os.path.join(LLM_DIR, "groq-runs.json")
LOG = os.path.join(LLM_DIR, "groq-auto.log")
MODEL = "qwen/qwen3.8-27b"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
GEOCODER_RUNS = os.path.join(os.path.dirname(TOOLS), "data", "geocoder-runs.json")
YANDEX_REQUESTS = 500  # на кандидатов модели; ещё до 300 — на ненайденные пары


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M')} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(*args, script="llm_places.py", env=ENV):
    """Шаг конвейера (по умолчанию llm_places.py); вывод — в журнал."""
    r = subprocess.run([sys.executable, os.path.join(TOOLS, script), *args], cwd=TOOLS,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
    for line in tail:
        log("  " + line)
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=180)
    ap.add_argument("--force", action="store_true", help="отправить, даже если сегодня уже отправляли")
    ap.add_argument("--no-yandex", action="store_true", help="только Groq")
    args = ap.parse_args()
    groq(args)
    if not args.no_yandex:
        yandex(args)


def yandex(args):
    today = time.strftime("%Y-%m-%d")
    runs = json.load(open(GEOCODER_RUNS, encoding="utf-8")) if os.path.exists(GEOCODER_RUNS) else {}
    if today in runs and not args.force:
        log("геокодер с Яндексом сегодня уже запускали — пропуск")
        return
    # суточный лимит ключа — 1000; сотню оставляем на поиск в форме точки
    env = dict(ENV, YANDEX_GEOCODER_LIMIT="900")
    # сперва — кандидаты модели у предложений с точкой на городе: 29.09 это
    # дало 9 точек на 270 запросов, а переспрос ненайденных пар разбора — 5
    # за весь день (там в основном мусор: «Общая площадь», чужие улицы).
    # Находки уходят в модерацию предложениями, точку утверждает админ
    found = os.path.join(LLM_DIR, f"pin-yandex-{today}.json")
    log(f"Яндекс по кандидатам модели: до {YANDEX_REQUESTS} запросов")
    run("yandex", "--max", str(YANDEX_REQUESTS), "--out", found, env=env)
    if os.path.exists(found) and json.load(open(found, encoding="utf-8")):
        run("pin", found)
    log("Яндекс по ненайденным парам разбора — остаток лимита")
    run("--only", "yandex", "--retry-missing", "--max-requests", "300",
        script="geocode_streets.py", env=env)
    run(script="classify.py")
    run(script="build_web_data.py")
    log("Яндекс: готово")


def groq(args):
    today = time.strftime("%Y-%m-%d")
    runs = json.load(open(RUNS, encoding="utf-8")) if os.path.exists(RUNS) else {}
    if today in runs and not args.force:
        log(f"сегодня в Groq уже отправляли ({sum(r['posts'] for r in runs[today])} постов) — пропуск")
        return

    # с запасом: часть отобранных уже лежит в несобранных ответах
    if not run("select", "--limit", str(args.limit * 3), "--batch", "25"):
        log("select не удался")
        return
    asked = set()
    numbers = []
    for f in glob.glob(os.path.join(LLM_DIR, "answer-*.json")):
        m = re.search(r"answer-(\d+)\.json$", f)
        if m:
            numbers.append(int(m.group(1)))
        try:
            asked |= {a.get("key") for a in json.load(open(f, encoding="utf-8"))}
        except (ValueError, OSError):
            pass
    rows = []
    for f in sorted(glob.glob(os.path.join(LLM_DIR, "batch-*.json"))):
        rows += json.load(open(f, encoding="utf-8"))
        os.remove(f)
    fresh = [r for r in rows if r["key"] not in asked][:args.limit]
    if not fresh:
        log("новых трудных постов нет")
        return
    for i in range(0, len(fresh), 25):
        json.dump(fresh[i:i + 25], open(os.path.join(LLM_DIR, f"batch-{i // 25 + 1:02d}.json"), "w",
                                         encoding="utf-8"), ensure_ascii=False, indent=1)
    out = os.path.join(LLM_DIR, f"answer-{max(numbers, default=0) + 1:02d}.json")
    log(f"в Groq: {len(fresh)} постов -> {os.path.basename(out)}")
    run("ask", *sorted(glob.glob(os.path.join(LLM_DIR, "batch-*.json"))), "--model", MODEL, "--out", out)
    if os.path.exists(out):
        # локальный сервер может быть не запущен — тогда merge пишет только
        # data/llm/suggestions.json, в модерацию их потом отправит post-suggestions
        run("merge", out)
    log("готово")


if __name__ == "__main__":
    # Задание Планировщика запускает pythonw.exe — без окна: 30.09 python.exe
    # открыл консоль, её закрыли, и прогон оборвался на 10 постах из 180
    # (код 0xC000013A), не оставив в журнале ни слова. Теперь и падение пишем
    try:
        main()
    except BaseException as e:
        import traceback
        log("ОШИБКА: " + " | ".join(traceback.format_exception(e)).strip().replace(chr(10), " "))
        raise
