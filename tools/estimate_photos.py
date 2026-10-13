# -*- coding: utf-8 -*-
"""Считает, сколько займут фотографии на сервере.

Исходники из выгрузки Telegram класть на хостинг незачем: карточке хватает
ширины 400 пикселей, окну поста — 1200. Скрипт берёт случайную выборку,
ужимает её в памяти и по полученному коэффициенту оценивает весь набор.

  python tools/estimate_photos.py            # все группы
  python tools/estimate_photos.py ru         # только российские города
"""
import io, json, os, random, sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sources import BY_KEY

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = 60
SIZES = [("карточка и карта", 400, 78), ("средняя", 800, 80),
         ("окно поста", 1000, 82), ("окно поста", 1280, 82)]


def photo_path(post, name):
    return os.path.join(BASE, BY_KEY[post["src"]]["dir"], "photos", name)


def photo_width(path):
    try:
        with Image.open(path) as im:
            return im.width
    except OSError:
        return 0


def resized_size(path, width, quality):
    with Image.open(path) as im:
        im = im.convert("RGB")
        if im.width > width:
            height = round(im.height * width / im.width)
            im = im.resize((width, height), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
        return buf.tell()


def main():
    group = sys.argv[1] if len(sys.argv) > 1 else None
    with open(os.path.join(BASE, "data", "posts_classified.json"), encoding="utf-8") as f:
        posts = [p for p in json.load(f) if not group or p["group"] == group]

    first, rest = [], []
    for p in posts:
        for i, name in enumerate(p["photos"][:6]):
            (first if i == 0 else rest).append((p, name))

    def total_bytes(items):
        out = 0
        for p, name in items:
            try:
                out += os.path.getsize(photo_path(p, name))
            except OSError:
                pass
        return out

    first_raw, rest_raw = total_bytes(first), total_bytes(rest)
    print("группа: %s" % (group or "все"))
    print("постов: %d, из них с фото: %d" % (len(posts), len(first)))
    print("файлов: первые %d, остальные %d" % (len(first), len(rest)))
    print("исходники: первые %.2f ГБ, все %.2f ГБ"
          % (first_raw / 1e9, (first_raw + rest_raw) / 1e9))

    # коэффициент сжатия меряем на выборке, а не угадываем
    random.seed(1)
    sample = random.sample(first, min(SAMPLE, len(first)))
    widths = sorted(
        w for w in (photo_width(photo_path(p_, n)) for p_, n in sample) if w
    )
    print("\nвыборка из %d фотографий, ширина оригиналов: медиана %d, максимум %d"
          % (len(sample), widths[len(widths) // 2], widths[-1]))
    ratios = {}
    for title, width, quality in SIZES:
        src = out = 0
        for p, name in sample:
            path = photo_path(p, name)
            try:
                out += resized_size(path, width, quality)
                src += os.path.getsize(path)
            except OSError:
                pass
        ratios[width] = out / src if src else 0
        print("  %-18s ширина %-5d -> %.1f%% от исходника, в среднем %.0f КБ"
              % (title, width, 100 * ratios[width], out / len(sample) / 1024))

    all_raw = first_raw + rest_raw
    small = ratios[400]
    print("\nоценка для сервера:")
    print("  превью 400px только для первого фото поста:  %.2f ГБ"
          % (first_raw * small / 1e9))
    print("  превью 400px для всех используемых фото:     %.2f ГБ"
          % (all_raw * small / 1e9))
    for w in (800, 1000, 1280):
        print("  превью 400px + %-4d px для окна поста:       %.2f ГБ"
              % (w, all_raw * (small + ratios[w]) / 1e9))
    print("  превью 400px + исходники для окна поста:     %.2f ГБ"
          % (first_raw * small / 1e9 + all_raw / 1e9))


if __name__ == "__main__":
    main()
