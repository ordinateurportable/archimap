#!/usr/bin/env bash
# Выкладка готового архива на стенд — с компьютера, где лежит проект.
# Запускать в Git Bash из папки проекта, VPN выключен (Рег.ру не пускает
# зарубежные адреса):
#   bash deploy/upload.sh
# Архив archimap-deploy.tar собирается заранее: web/ (dist), server/, deploy/, llm/.
# Что делает: загружает архив, ставит его deploy/install.sh (резервные копии,
# server/.env и data/ не трогает), дописывает в server/.env ключ Яндекс-
# геокодера, если его там нет (ключ не печатается), отправляет предложения
# модели и решения локальной модерации, проверяет ручки.
set -euo pipefail
cd "$(dirname "$0")/.."

# Всё, что скрипт печатает, — и на экран, и в deploy/upload.log: выкладывает
# пользователь (из сессии Claude стенд не виден), а итог читает Claude —
# файл перезаписывается при каждом запуске (13.10)
exec > >(tee deploy/upload.log) 2>&1
echo "== выкладка $(date '+%d.%m.%Y %H:%M')"

# Куда выкладывать — в deploy/target.env (в git не идёт, образец —
# deploy/target.env.example): адрес сервера и ключ SSH не публикуем
. "$(dirname "$0")/target.env" 2>/dev/null || { echo "нет deploy/target.env — скопируйте deploy/target.env.example и впишите свой сервер"; exit 1; }
HOST=$ARCHIMAP_HOST
KEY=$ARCHIMAP_KEY
SITE=$ARCHIMAP_SITE
ssh_() { ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=20 "$HOST" "$@"; }

echo "== связь со стендом"
ssh_ 'echo "   ok: $(hostname), свободно $(df -h / | tail -1 | awk "{print \$4}")"'

# Связь со стендом рвётся на длинной передаче (30.09: «lost connection» на
# 18% из 26 МБ), поэтому архив идёт кусками по 4 МБ, каждый — с повторами;
# кусок, который уже лежит на стенде (та же контрольная сумма), второй раз не
# шлём — повторный запуск докачивает только недостающее. На сервере куски
# склеиваются, и перед установкой сверяется контрольная сумма
echo "== загрузка архива ($(du -h archimap-deploy.tar | cut -f1)) кусками"
PARTS=$(mktemp -d)
trap 'rm -rf "$PARTS"' EXIT
split -b 4m -d -a 3 archimap-deploy.tar "$PARTS/part-"
SUM=$(sha256sum archimap-deploy.tar | cut -d' ' -f1)
ssh_ 'mkdir -p /tmp/archimap-parts'
have=$(ssh_ 'cd /tmp/archimap-parts && sha256sum part-* 2>/dev/null' || true)
for part in "$PARTS"/part-*; do
  name=$(basename "$part")
  sha=$(sha256sum "$part" | cut -d' ' -f1)
  if grep -qx "$sha  $name" <<<"$have"; then
    echo "   $name — уже на стенде"
    continue
  fi
  for try in 1 2 3 4 5; do
    if scp -q -i "$KEY" -o BatchMode=yes -o ConnectTimeout=20 "$part" "$HOST:/tmp/archimap-parts/$name"; then
      echo "   $name — ок"
      break
    fi
    [ "$try" = 5 ] && { echo "!! $name не загрузился за 5 попыток — запустите скрипт ещё раз, загруженное не пропадёт"; exit 1; }
    echo "   $name — обрыв, повтор через 5 с ($try/5)"
    sleep 5
  done
done
ssh_ "cd /tmp/archimap-parts && ls part-* | sort | head -n $(ls "$PARTS" | wc -l) | xargs cat > /tmp/archimap-deploy.tar \
  && echo '$SUM  /tmp/archimap-deploy.tar' | sha256sum -c --quiet \
  && rm -rf /tmp/archimap-parts && echo '   архив собран, контрольная сумма совпала'" \
  || { echo "!! архив на стенде не совпал с местным — запустите скрипт ещё раз"; ssh_ 'rm -rf /tmp/archimap-parts'; exit 1; }

echo "== установка"
# скрипты правятся под Windows и могут прийти с CRLF — bash на сервере
# тогда падает на первой же строке («set: pipefail\r», 30.09)
ssh_ 'rm -rf /tmp/archimap-new && mkdir -p /tmp/archimap-new \
  && tar xf /tmp/archimap-deploy.tar -C /tmp/archimap-new --no-same-owner \
  && sed -i "s/\r$//" /tmp/archimap-new/deploy/*.sh /tmp/archimap-new/deploy/*.service \
  && bash /tmp/archimap-new/deploy/install.sh'

echo "== ключ Яндекс-геокодера"
if ssh_ 'grep -q "^YANDEX_GEOCODER_KEY=" /srv/archimap/server/.env'; then
  echo "   уже есть в server/.env"
elif [ -f tools/.yandex-geocoder-key ]; then
  # ключ идёт через stdin ssh — ни в командной строке, ни на экране его нет
  printf '\nYANDEX_GEOCODER_KEY=%s\n' "$(tr -d '\r\n' < tools/.yandex-geocoder-key)" \
    | ssh_ 'cat >> /srv/archimap/server/.env && systemctl restart archimap-api && sleep 2'
  echo "   дописан, служба перезапущена"
else
  echo "   !! нет tools/.yandex-geocoder-key — поиск на Яндекс-карте в модерации работать не будет"
fi

# только если они в архиве: повторная отправка вернула бы предложения,
# которые админ на стенде уже удалил
echo "== предложения модели и решения локальной модерации"
ssh_ 'cd /srv/archimap
  if [ -f /tmp/archimap-new/llm/suggestions.json ]; then
    node --env-file=server/.env deploy/post-suggestions.mjs /tmp/archimap-new/llm/suggestions.json
  else echo "   предложений в архиве нет — пропуск"; fi
  if [ -f /tmp/archimap-new/llm/local-edits.json ]; then
    node --env-file=server/.env deploy/post-edits.mjs /tmp/archimap-new/llm/local-edits.json
  else echo "   локальных правок в архиве нет — пропуск"; fi'

echo "== проверка"
code() { curl -s -o /dev/null -w "%{http_code}" -m 20 "$@"; }
echo "   сайт:                     $(code "$SITE/") (ждём 200)"
echo "   /api/user-posts:          $(code "$SITE/api/user-posts") (ждём 200)"
echo "   ручка админа без входа:   $(code -X POST "$SITE/api/edits/arkh-1/location") (ждём 401)"
echo "   Яндекс-геокодер без входа: $(code "$SITE/api/yandex-geocode?q=test") (ждём 401)"
echo "   поиск места без входа:    $(code "$SITE/api/place-search?q=test") (ждём 401)"
echo "   шрифт запасной карты:     $(code "$SITE/fonts/Noto%20Sans%20Regular/0-255.pbf") (ждём 200)"
echo "   карта через наш сервер:   $(code "$SITE/ofm/styles/positron") (ждём 200)"
echo "   тайл через наш сервер:    $(code "$SITE/ofm/planet") (ждём 200)"
echo "   запасная через наш сервер: $(code "$SITE/osm/5/19/9.png") (ждём 200)"

# правки, сделанные на стенде, — обратно к конвейеру (tools/edits_store.py):
# без этого ручные точки со стенда не шли модели в примеры и в словарь мест
echo "== правки со стенда"
bash deploy/pull-edits.sh || echo "   не скачались — повторить: bash deploy/pull-edits.sh"
echo "готово"
