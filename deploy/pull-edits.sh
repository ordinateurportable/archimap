#!/usr/bin/env bash
# Правки админа со стенда -> data/edits-stand.json. Конвейер (tools/
# edits_store.py) читает их вместе с локальным data/edits.json: ручные точки
# со стенда идут модели в примеры, в словарь найденных мест, решают вкладку
# поста, а посты с ними больше не уходят модели (15.10). Запускать в Git Bash
# из папки проекта без VPN — сам или его вызывает deploy/upload.sh в конце
# выкладки:
#   bash deploy/pull-edits.sh
set -euo pipefail
cd "$(dirname "$0")/.."
. deploy/target.env 2>/dev/null || { echo "нет deploy/target.env — скопируйте deploy/target.env.example и впишите свой сервер"; exit 1; }

part=data/edits-stand.json.part
ssh -i "$ARCHIMAP_KEY" -o BatchMode=yes -o ConnectTimeout=20 "$ARCHIMAP_HOST" 'cat /srv/archimap/data/edits.json' > "$part"
# недокачанный или битый файл прежнюю копию не заменяет
if PYTHONIOENCODING=utf-8 python -c "import json,sys; e=json.load(open(sys.argv[1],encoding='utf-8')); print('   ручных точек %d, скрытых %d, предложений модели %d, заявок на правила %d' % (sum(1 for v in e.get('edits',{}).values() if (v or {}).get('location')), len(e.get('hidden',{})), len(e.get('suggestions',[])), len(e.get('rules',[]))))" "$part"; then
  mv "$part" data/edits-stand.json
  echo "   записано в data/edits-stand.json"
else
  rm -f "$part"
  echo "!! правки со стенда не прочитались — прежняя копия не тронута"
  exit 1
fi
