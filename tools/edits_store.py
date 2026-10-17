# -*- coding: utf-8 -*-
"""Правки админа для конвейера: локальный data/edits.json вместе со
стендовым data/edits-stand.json.

Раньше конвейер видел только локальные правки: точки, поставленные на
стенде, не шли модели в примеры, не попадали в словарь найденных мест,
не меняли вкладку поста при сборке, а посты с ними снова уходили модели
(15.10). Стендовую копию скачивает deploy/pull-edits.sh — сам и в конце
каждой выкладки (deploy/upload.sh), из сессии Claude стенд не виден.

Склейка — как в deploy/post-edits.mjs: правка, сделанная на стенде, важнее
локальной. Ручные точки и подписи — по ключу поста, стендовые встают в
конец (select берёт последние как свежие примеры); предложения модели — по
id, со стендовым статусом («на доработку», «не реклама»); скрытые,
отмеченные, решённые в отчётах — объединение.
"""
import json, os

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
LOCAL = os.path.join(DATA, "edits.json")
STAND = os.path.join(DATA, "edits-stand.json")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def load_edits(path=None):
    """path — один конкретный файл (select --edits); без него — склейка."""
    if path:
        return _read(path)
    local, stand = _read(LOCAL), _read(STAND)
    if not stand:
        return local
    edits = dict(local.get("edits", {}))
    for k, v in stand.get("edits", {}).items():
        edits.pop(k, None)
        edits[k] = v
    out = {"edits": edits}
    for name in ("hidden", "flagged", "reviewed", "bugs"):
        out[name] = {**local.get(name, {}), **stand.get(name, {})}
    for name in ("suggestions", "rules"):
        rows = {r.get("id"): r for r in local.get(name, [])}
        rows.update({r.get("id"): r for r in stand.get(name, [])})
        out[name] = list(rows.values())
    return out


def stand_date():
    """Когда скачана стендовая копия — для строки в журналах."""
    return os.path.getmtime(STAND) if os.path.exists(STAND) else None
