# -*- coding: utf-8 -*-
"""Готовит фотографии для сервера.

Исходники из выгрузки Telegram весят 12.5 ГБ, на хостинг их класть незачем:
карточке и карте хватает ширины 400 пикселей, окну поста — 800.

Правило раскладки одно на весь проект и повторено во фронтенде
(web/src/data.js, функция photoURL): первое фото поста получает обе
версии, остальные — только превью. Первое видно в карточке и открывается
крупно, остальные листают внутри окна.

  python tools/build_photos.py               # группа ru, 400 и 800
  python tools/build_photos.py --group all   # все посты
  python tools/build_photos.py --large 0     # только превью, самый лёгкий
                                             # вариант

Результат — photos-web/<ширина>/<канал>/<файл>. Повторный запуск
пропускает готовое, так что прерванную работу можно продолжить.
"""
import argparse, json, os, sys
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sources import BY_KEY

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_PHOTOS = 6  # столько же берёт build_web_data.py


def convert(src, dst, width, quality):
    """Ужимает одну фотографию. Возвращает размер результата в байтах."""
    if os.path.exists(dst):
        return os.path.getsize(dst)
    try:
        with Image.open(src) as im:
            im = im.convert("RGB")
            if im.width > width:
                height = round(im.height * width / im.width)
                im = im.resize((width, height), Image.LANCZOS)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            tmp = dst + ".tmp"
            im.save(tmp, "JPEG", quality=quality, optimize=True, progressive=True)
            os.replace(tmp, dst)
        return os.path.getsize(dst)
    except Exception as e:
        print("  не вышло:", os.path.basename(src), e)
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="ru", help="ru, foreign, none или all")
    ap.add_argument("--out", default=os.path.join(BASE, "photos-web"))
    ap.add_argument("--small", type=int, default=400, help="ширина превью")
    ap.add_argument("--large", type=int, default=800,
                    help="ширина для окна поста; 0 — не делать вовсе")
    ap.add_argument("--quality", type=int, default=80)
    ap.add_argument("--workers", type=int, default=min(8, (os.cpu_count() or 2)))
    args = ap.parse_args()

    with open(os.path.join(BASE, "data", "posts_classified.json"), encoding="utf-8") as f:
        posts = json.load(f)
    if args.group != "all":
        posts = [p for p in posts if p["group"] == args.group]

    jobs = []
    for post in posts:
        photos_dir = os.path.join(BASE, BY_KEY[post["src"]]["dir"], "photos")
        for i, name in enumerate(post["photos"][:MAX_PHOTOS]):
            src = os.path.join(photos_dir, name)
            jobs.append((src, os.path.join(args.out, str(args.small), post["src"], name),
                         args.small))
            # крупная версия — только у первого фото поста
            if i == 0 and args.large:
                jobs.append((src, os.path.join(args.out, str(args.large), post["src"], name),
                             args.large))

    print("группа: %s, постов: %d, файлов к обработке: %d"
          % (args.group, len(posts), len(jobs)))
    print("куда: %s" % args.out)

    done = total = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(convert, s, d, w, args.quality) for s, d, w in jobs]
        for fut in futures:
            total += fut.result()
            done += 1
            if done % 2000 == 0:
                print("  %d из %d, накоплено %.2f ГБ" % (done, len(jobs), total / 1e9))

    print("готово: %d файлов, %.2f ГБ" % (done, total / 1e9))


if __name__ == "__main__":
    main()
