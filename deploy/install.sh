#!/usr/bin/env bash
# Установка новой версии на сервер из распакованного архива.
#
#   mkdir -p /tmp/archimap-new
#   tar xf /tmp/archimap-deploy.tar -C /tmp/archimap-new --no-same-owner
#   bash /tmp/archimap-new/deploy/install.sh
#
# Что делает: копирует сайт и код сервера правок, добавляет новые
# фотографии (существующие не трогает), вставляет в конфиг nginx блок для
# постов посетителей и перезапускает службу. Перед этим кладёт резервные
# копии. server/.env, node_modules и data/ не трогает.
set -euo pipefail

NEW="$(cd "$(dirname "$0")/.." && pwd)"
SRV=/srv/archimap
CONF=/etc/nginx/sites-available/archimap
TS=$(date +%Y%m%d-%H%M%S)

echo "== резервные копии"
tar czf "$SRV/backup-$TS.tgz" -C "$SRV" --exclude=server/node_modules web server
cp "$CONF" "$CONF.bak-$TS"
echo "   $SRV/backup-$TS.tgz, $CONF.bak-$TS"

echo "== сайт"
cp -r "$NEW/web/." "$SRV/web/"

echo "== сервер правок"
cp "$NEW/server/index.js" "$NEW/server/package.json" "$NEW/server/package-lock.json" \
   "$NEW/server/.env.example" "$SRV/server/"
mkdir -p "$SRV/deploy" && cp -r "$NEW/deploy/." "$SRV/deploy/"
(cd "$SRV/server" && npm install --omit=dev --no-audit --no-fund)

echo "== фотографии (только новые)"
if [ -d "$NEW/photos-web" ]; then
  cp -rn "$NEW/photos-web/." "$SRV/photos-web/"
fi

# посты посетителей и их фотографии сервер пишет в data/
mkdir -p "$SRV/data/uploads"
chown -R www-data "$SRV/data"

echo "== nginx"
if grep -q "location = /api/user-posts" "$CONF"; then
  echo "   блок для /api/user-posts уже есть"
else
  # Вставляем блок перед каждым «location /api/ {»: если certbot разделил
  # конфиг на http и https, блок нужен там, где проксируется /api/
  python3 - "$CONF" <<'PY'
import re, sys
path = sys.argv[1]
s = open(path, encoding="utf-8").read()
block = """    # Посты посетителей приходят вместе с фотографиями (до четырёх,
    # каждая уже ужата браузером), поэтому здесь предел больше общего.
    location = /api/user-posts {
        client_max_body_size 16m;
        proxy_pass http://127.0.0.1:5174;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

"""
new, n = re.subn(r"(?m)^([ \t]*)location /api/ \{", lambda m: block + m.group(0), s)
if not n:
    sys.exit("в конфиге не нашлось «location /api/ {» — добавьте блок вручную")
open(path, "w", encoding="utf-8").write(new)
print(f"   добавлен блок в {n} мест(а)")
PY
fi
# index.html без заголовков кэша браузер держит «по эвристике» часами — после
# выкладки у админа открывалась старая модерация (29.09). Страницу — всегда
# сверять с сервером; файлы сборки с хэшем в имени и так кэшируются навсегда
if grep -q "location = /index.html" "$CONF"; then
  echo "   правило кэша для index.html уже есть"
else
  python3 - "$CONF" <<'PY'
import re, sys
path = sys.argv[1]
s = open(path, encoding="utf-8").read()
block = """    # страницу браузер сверяет с сервером при каждом заходе: после выкладки
    # сразу новая версия
    location = /index.html {
        add_header Cache-Control "no-cache";
    }
    location = / {
        add_header Cache-Control "no-cache";
        try_files /index.html =404;
    }

"""
new, n = re.subn(r"(?m)^([ \t]*)location /assets/ \{", lambda m: block + m.group(0), s)
if not n:
    sys.exit("в конфиге не нашлось «location /assets/ {» — добавьте правило для index.html вручную")
open(path, "w", encoding="utf-8").write(new)
print(f"   правило кэша для index.html добавлено в {n} мест(а)")
PY
fi
# подложка карты через наш сервер (30.09): без VPN из России OpenFreeMap
# и tile.openstreetmap.org не грузятся, а со стенда отвечают за 0,1 с
if grep -q "location /ofm/" "$CONF"; then
  echo "   прокси подложки карты уже есть"
else
  mkdir -p /var/cache/nginx/archimap-tiles
  chown www-data /var/cache/nginx/archimap-tiles
  python3 - "$CONF" <<'PY'
import re, sys
path = sys.argv[1]
s = open(path, encoding="utf-8").read()
cache = """# Кэш подложки карты (location /ofm/ и /osm/ ниже): 500 МБ — на диске
# стенда всего около 3 ГБ свободно
proxy_cache_path /var/cache/nginx/archimap-tiles levels=1:2 keys_zone=archimap_tiles:20m max_size=500m inactive=30d use_temp_path=off;

"""
block = """    # Подложка карты — через наш сервер: из России без VPN OpenFreeMap
    # отвечает и виснет, tile.openstreetmap.org не отвечает вовсе, а сервер
    # забирает те же тайлы за 0,1 с (deploy/check-map.sh, 30.09). Кэш —
    # archimap_tiles (proxy_cache_path в начале файла). Стили и описание
    # тайлов меняются — час; тайлы, шрифты и спрайты с версией в пути — месяц.
    location /ofm/ {
        proxy_pass https://tiles.openfreemap.org/;
        proxy_ssl_server_name on;
        proxy_set_header Host tiles.openfreemap.org;
        proxy_set_header Cookie "";
        proxy_http_version 1.1;
        proxy_cache archimap_tiles;
        proxy_cache_valid 200 1h;
        proxy_cache_valid any 1m;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503 http_504;
        proxy_cache_lock on;
        proxy_ignore_headers Cache-Control Expires Set-Cookie;
        add_header X-Cache $upstream_cache_status;
        expires 1h;
    }
    location ~ ^/ofm/(planet/[^/]+/|fonts/|sprites/) {
        rewrite ^/ofm/(.*)$ /$1 break;
        proxy_pass https://tiles.openfreemap.org;
        proxy_ssl_server_name on;
        proxy_set_header Host tiles.openfreemap.org;
        proxy_set_header Cookie "";
        proxy_http_version 1.1;
        proxy_cache archimap_tiles;
        proxy_cache_valid 200 30d;
        proxy_cache_valid any 1m;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503 http_504;
        proxy_cache_lock on;
        proxy_ignore_headers Cache-Control Expires Set-Cookie;
        add_header X-Cache $upstream_cache_status;
        expires 30d;
    }
    # запасная подложка — растровые тайлы OpenStreetMap, тоже через нас
    location /osm/ {
        proxy_pass https://tile.openstreetmap.org/;
        proxy_ssl_server_name on;
        proxy_set_header Host tile.openstreetmap.org;
        proxy_set_header User-Agent "archimap/1.0 (+https://archimap.194.226.142.99.sslip.io)";
        proxy_set_header Cookie "";
        proxy_http_version 1.1;
        proxy_cache archimap_tiles;
        proxy_cache_valid 200 7d;
        proxy_cache_valid any 1m;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503 http_504;
        proxy_cache_lock on;
        proxy_ignore_headers Cache-Control Expires Set-Cookie;
        add_header X-Cache $upstream_cache_status;
        expires 7d;
    }

"""
if "keys_zone=archimap_tiles" not in s:
    s = cache + s
new, n = re.subn(r"(?m)^([ 	]*)location /assets/ \{", lambda m: block + m.group(0), s)
if not n:
    sys.exit("в конфиге не нашлось «location /assets/ {» — добавьте прокси подложки вручную (deploy/nginx.conf)")
open(path, "w", encoding="utf-8").write(new)
print(f"   прокси подложки карты добавлен в {n} мест(а)")
PY
fi
if ! nginx -t; then
  echo "!! nginx -t не прошёл — возвращаю прежний конфиг"
  cp "$CONF.bak-$TS" "$CONF"
  exit 1
fi
systemctl reload nginx

echo "== перезапуск службы"
systemctl restart archimap-api
sleep 2
systemctl is-active archimap-api
code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5174/api/user-posts)
echo "   /api/user-posts -> $code (должно быть 200)"
echo "готово. Откат: tar xzf $SRV/backup-$TS.tgz -C $SRV && cp $CONF.bak-$TS $CONF && systemctl reload nginx && systemctl restart archimap-api"
