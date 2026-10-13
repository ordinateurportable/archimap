#!/usr/bin/env bash
# Откуда грузится подложка карты: с этого компьютера (без VPN) и со стенда.
# Запускать в Git Bash из папки проекта, VPN выключен:
#   bash deploy/check-map.sh
# Если с компьютера OpenFreeMap не отвечает, а со стенда отвечает — тайлы
# можно отдавать через наш сервер (прокси в nginx).
# Куда выкладывать — в deploy/target.env (в git не идёт, образец —
# deploy/target.env.example): адрес сервера и ключ SSH не публикуем
. "$(dirname "$0")/target.env" 2>/dev/null || { echo "нет deploy/target.env — скопируйте deploy/target.env.example и впишите свой сервер"; exit 1; }
HOST=$ARCHIMAP_HOST
KEY=$ARCHIMAP_KEY

probe='
t() { printf "   %-34s %s\n" "$1" "$(curl -s -o /dev/null -m 15 -w "%{http_code} за %{time_total}с" "$2" || echo "нет ответа")"; }
t "OpenFreeMap стиль"          https://tiles.openfreemap.org/styles/positron
t "OpenFreeMap описание тайлов" https://tiles.openfreemap.org/planet
tile=$(curl -s -m 15 https://tiles.openfreemap.org/planet | grep -o "https://tiles.openfreemap.org/planet/[^\"{]*" | head -1)
[ -n "$tile" ] && t "OpenFreeMap тайл"         "${tile}0/0/0.pbf"
t "OpenStreetMap тайл"         https://tile.openstreetmap.org/5/19/9.png
t "Carto тайл"                 https://a.basemaps.cartocdn.com/light_all/5/19/9.png
t "2ГИС тайл"                  "https://tile2.maps.2gis.com/tiles?x=19&y=9&z=5"
'

echo "== с этого компьютера"
bash -c "$probe"
echo "== со стенда"
ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=20 "$HOST" "bash -c '$probe'" 2>&1 || echo "   стенд не ответил"
