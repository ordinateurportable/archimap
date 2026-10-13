# -*- coding: utf-8 -*-
"""Превращает выгрузки Telegram в плоский список постов.

Телеграм выгружает альбом как несколько сообщений с одинаковой датой,
подпись есть только у одного из них. Здесь такие сообщения склеиваются
в один пост.

Каналов может быть несколько, они перечислены в sources.py. Номера
сообщений в разных каналах совпадают, поэтому у поста есть ещё и поле
src — вместе с id оно и даёт уникальный ключ.

На выходе data/posts.json — список:
  {"id", "src", "date", "text", "photos": [...], "author", "fwd", "url"}
"""
import json, os, sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sources import SOURCES

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def plain_text(msg):
    """Текст сообщения: text_entities всегда плоский список, в отличие от text."""
    parts = []
    for e in msg.get("text_entities", []):
        t = e.get("text", "")
        if e["type"] == "text_link" and e.get("href"):
            t = "%s (%s)" % (t, e["href"])
        parts.append(t)
    return "".join(parts).strip()


def load_messages(export_dir):
    with open(os.path.join(export_dir, "result.json"), encoding="utf-8") as f:
        data = json.load(f)
    return [m for m in data["messages"] if m.get("type") == "message"]


def group_albums(msgs):
    """Подряд идущие сообщения с одинаковой датой — один пост.

    Возвращает пары (сообщения, заглавное): по заглавному — номер поста,
    дата и ссылка."""
    groups, cur = [], [msgs[0]]
    for m in msgs[1:]:
        if m["date"] == cur[-1]["date"]:
            cur.append(m)
        else:
            groups.append(cur)
            cur = [m]
    groups.append(cur)
    return join_split_albums([(g, g[0]) for g in groups])


def seconds(msg):
    return datetime.fromisoformat(msg["date"]).timestamp()


def join_split_albums(groups):
    """Альбом, разрезанный границей секунды: «Тула» — три фото без подписи
    в 11:18:59 и подпись с шестью фото в 11:19:00 — вышли двумя постами,
    и фото без текста «отваливались» от поста. Склеиваем соседние группы,
    если между ними не больше двух секунд, номера сообщений идут подряд,
    фото есть в обеих, а текст — только в одной. Номер поста остаётся от
    группы с текстом: к нему привязаны правки и предложения модели."""
    out = []
    for msgs, head in groups:
        if out:
            prev, prev_head = out[-1]
            has_text = [any(plain_text(m) for m in g) for g in (prev, msgs)]
            if (seconds(msgs[0]) - seconds(prev[-1]) <= 2
                    and msgs[0]["id"] - prev[-1]["id"] <= 3
                    and all(any(m.get("photo") for m in g) for g in (prev, msgs))
                    and not all(has_text)):
                out[-1] = (prev + msgs, head if has_text[1] else prev_head)
                continue
        out.append((msgs, head))
    return attach_orphan_photos(out)


def attach_orphan_photos(groups):
    """Ранние посты канала: текст без фото, а снимок отправлен отдельным
    сообщением на несколько секунд раньше или позже («Какое из этих зданий
    находится не в России?» и фото перед опросом). Фото без текста
    прикрепляем к соседнему посту с текстом и без фото — если между ними
    не больше минуты и номера идут подряд; из двух соседей — к ближайшему."""
    def text_of(g):
        return any(plain_text(m) for m in g[0])

    def photos_of(g):
        return any(m.get("photo") for m in g[0])

    target = {}  # номер группы-сироты -> номер группы с текстом
    for i, g in enumerate(groups):
        if text_of(g) or not photos_of(g):
            continue
        best = None
        for j in (i - 1, i + 1):
            if not 0 <= j < len(groups) or not text_of(groups[j]) or photos_of(groups[j]):
                continue
            a, b = (groups[j][0][-1], g[0][0]) if j < i else (g[0][-1], groups[j][0][0])
            gap = seconds(b) - seconds(a)
            if 0 <= gap <= 60 and b["id"] - a["id"] <= 3 and (best is None or gap < best[0]):
                best = (gap, j)
        if best:
            target[i] = best[1]
    out = []
    for i, (msgs, head) in enumerate(groups):
        if i in target:
            continue
        before = [m for k, j in target.items() if j == i and k < i for m in groups[k][0]]
        after = [m for k, j in target.items() if j == i and k > i for m in groups[k][0]]
        out.append((before + msgs + after, head))
    return out


def make_post(group, source, head=None):
    texts = [t for t in (plain_text(m) for m in group) if t]
    photos = [m["photo"].split("/")[-1] for m in group if m.get("photo")]
    head = head or group[0]
    return {
        "id": head["id"],
        "src": source["key"],
        "date": head["date"],
        "text": "\n\n".join(texts),
        "photos": photos,
        "author": head.get("author"),
        "fwd": head.get("forwarded_from"),
        "url": "https://t.me/%s/%d" % (source["channel"], head["id"]),
        "reactions": sum(r.get("count", 0) for r in head.get("reactions", [])),
    }


def main():
    posts = []
    for source in SOURCES:
        export_dir = os.path.join(BASE, source["dir"])
        if not os.path.isdir(export_dir):
            print("нет папки %s — канал «%s» пропущен" % (source["dir"], source["title"]))
            continue
        msgs = load_messages(export_dir)
        groups = group_albums(msgs)
        made = [make_post(g, source, head) for g, head in groups]
        posts.extend(made)
        print("%-24s %6d сообщений -> %5d постов" % (source["title"], len(msgs), len(made)))

    posts.sort(key=lambda p: p["date"])
    out = os.path.join(BASE, "data", "posts.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False)
    print("всего: %d постов, с текстом %d, с фото %d" % (
        len(posts),
        sum(1 for p in posts if p["text"]),
        sum(1 for p in posts if p["photos"]),
    ))
    print("->", out)


if __name__ == "__main__":
    main()
