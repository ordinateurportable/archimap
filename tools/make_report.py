"""Отчёт о круге разбора постов -> data/reports.json (вкладка «Отчёты» в
модерации; на сайт уходит через build_web_data.py).

Считает по ответам модели (answer-NN.json) и заданиям поиска точек
(pin-in/out/skip-NN.json): сколько постов, у скольких точка на объекте, на
городе, без точки; сколько уточнил Sonnet и свои справочники. И два списка с
причинами — их просил пользователь: посты, где модель не назвала места
(причина — поле reason ответа), и где искатель не нашёл объект (pin-skip).

    python tools/make_report.py --id 1009-300 --title "300 постов: Haiku + Sonnet" \\
        --answers 134-145 --pins 275-289 --models "Claude Haiku 4.5, Claude Sonnet 5.5" \\
        --note "что поправлено руками" --note "…"
    python tools/make_report.py --refresh     # пересчитать все отчёты по их файлам

У каждого числа — список его постов (третье поле в stats): на сайте плашка
нажимается и показывает эти посты (09.10, просьба пользователя). Отчёт
помнит свои номера файлов (answers, pins), --refresh пересчитывает всё по
ним — числа тогда по нынешнему состоянию (поставленные позже точки учтены),
название, заметки и дата остаются.
"""
import argparse, datetime, json, os, re

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = os.path.join(BASE, "data")
LLM = os.path.join(DATA, "llm")
OUT = os.path.join(DATA, "reports.json")


def span(text):
    a, _, b = text.partition("-")
    return range(int(a), int(b or a) + 1)


def load(path, default):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else default


def title_of(post):
    first = re.split(r"[\n.!?]", (post.get("text") or "").strip(), maxsplit=1)[0]
    return first[:100] + ("…" if len(first) > 100 else "")


def build(answers_span, pins_span):
    """Числа и списки постов одного круга: (stats, noPlace, missed)."""
    store = load(os.path.join(DATA, "llm_places.json"), {})
    posts = {f"{p['src']}-{p['id']}": p for p in load(os.path.join(DATA, "posts_classified.json"), [])}
    answers = {}
    for n in span(answers_span):
        for a in load(os.path.join(LLM, f"answer-{n}.json"), []):
            answers[a["key"]] = a

    def item(k, note=""):
        return {"key": k, "title": title_of(posts.get(k, {})), "note": note}

    exact, city, no_place = [], [], []
    for k, a in answers.items():
        r = store.get(k, {})
        label = r.get("label") or ""
        if r.get("point") and " · " in label:
            exact.append(item(k, label))
        elif r.get("point"):
            city.append(item(k, label))
        else:
            no_place.append({"key": k, "title": title_of(posts.get(k, {})),
                             "reason": (a.get("reason") or "").rstrip(". ")
                             or ("подборка без одного главного места" if a.get("digest") else "место не определено")})

    sought, found, missed = set(), set(), []
    for n in (span(pins_span) if pins_span else []):
        sought |= {p["key"] for p in load(os.path.join(LLM, f"pin-in-{n}.json"), [])}
        found |= {p["key"] for p in load(os.path.join(LLM, f"pin-out-{n}.json"), [])}
        for s in load(os.path.join(LLM, f"pin-skip-{n}.json"), []):
            missed.append({"key": s["key"], "title": title_of(posts.get(s["key"], {})),
                           "object": s.get("object", ""), "reason": s.get("reason", "")})
    applied = {k for k in found if store.get(k, {}).get("pinned_by") not in (None, "local", "yandex")}
    noted = {m["key"] for m in missed} | applied
    for k in sorted(sought - noted):
        missed.append({"key": k, "title": title_of(posts.get(k, {})),
                       "object": ", ".join((store.get(k, {}).get("candidates") or [])[:2]),
                       "reason": "найдено, но не взято" if k in found else "не найдено (причина не записана)"})
    # свои справочники находят и сразу при поиске точек (point.src «local»),
    # и повторным проходом `llm_places.py local` (pinned_by «local»)
    local = [item(k, store[k].get("label") or "") for k in answers
             if store.get(k, {}).get("pinned_by") == "local"
             or (store.get(k, {}).get("point") or {}).get("src") == "local"]
    pinned = [item(k, store[k].get("label") or "") for k in answers if k in applied]
    food = [item(k) for k in answers if store.get(k, {}).get("food")]
    ads = [item(k, store[k]["ad"].strip()) for k in answers if (store.get(k, {}).get("ad") or "").strip()]
    stats = [
        ["Постов", len(answers), [item(k) for k in answers]],
        ["Точка на объекте, адресе или селе", len(exact), exact],
        ["Точка только на городе", len(city), city],
        ["Без точки", len(no_place), [item(x["key"], x["reason"]) for x in no_place]],
        ["Искатель уточнил точку", len(pinned), pinned],
        ["Нашли свои справочники", len(local), local],
        ["Тег «еда»", len(food), food],
        ["Возможная реклама", len(ads), ads],
    ]
    return stats, no_place, missed


def save(reports):
    json.dump(reports, open(OUT, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)


def summary(stats):
    return ", ".join(f"{name} {value}" for name, value, _ in stats)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id")
    ap.add_argument("--title")
    ap.add_argument("--answers", help="номера ответов: 134-145")
    ap.add_argument("--pins", default="", help="номера заданий поиска: 275-289")
    ap.add_argument("--models", default="")
    ap.add_argument("--note", action="append", default=[])
    ap.add_argument("--refresh", action="store_true", help="пересчитать все отчёты по их файлам")
    args = ap.parse_args()

    if args.refresh:
        reports = load(OUT, [])
        for r in reports:
            if not r.get("answers"):
                print(f"{r['id']}: номера файлов не записаны — пропускаю")
                continue
            r["stats"], r["noPlace"], r["missed"] = build(r["answers"], r.get("pins", ""))
            print(f"{r['id']}: {summary(r['stats'])}")
        save(reports)
        return

    if not (args.id and args.title and args.answers):
        ap.error("нужны --id, --title и --answers (или --refresh)")
    stats, no_place, missed = build(args.answers, args.pins)
    entry = {
        "id": args.id,
        "date": datetime.date.today().isoformat(),
        "title": args.title,
        "models": [m.strip() for m in args.models.split(",") if m.strip()],
        "answers": args.answers,
        "pins": args.pins,
        "stats": stats,
        "notes": args.note,
        "noPlace": no_place,
        "missed": missed,
    }
    reports = [r for r in load(OUT, []) if r["id"] != args.id]
    reports.insert(0, entry)
    save(reports)
    print(f"{args.title}: {summary(stats)}, не нашёл искатель {len(missed)} -> {OUT}")


if __name__ == "__main__":
    main()
