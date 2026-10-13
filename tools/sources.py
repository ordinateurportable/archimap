# -*- coding: utf-8 -*-
"""Каналы, выгрузки которых лежат в проекте.

Чтобы добавить ещё один канал, достаточно дописать сюда запись: остальные
скрипты и фронтенд берут список отсюда.

  key     — короткий идентификатор, он же папка в адресах фотографий
  short   — как канал подписан на карточке, где места мало
  dir     — папка с выгрузкой Telegram рядом с этим проектом
  channel — имя канала в t.me, из него собираются ссылки на посты
  title   — как канал называется в интерфейсе
"""

SOURCES = [
    {
        "key": "arkh",
        "dir": "Archi",
        "channel": "arkhlikbez",
        "title": "Архитектурные излишества",
        "short": "Излишества",
    },
    {
        "key": "swamps",
        "dir": "Swaps_and_churches",
        "channel": "swampsnchurches",
        "title": "Болота и церкви",
        "short": "Болота",
    },
    {
        "key": "ozzed",
        "dir": "ozzed expeditions",
        "channel": "ozzed_expeditions",
        "title": "ozzed expeditions",
        "short": "ozzed",
    },
]

BY_KEY = {s["key"]: s for s in SOURCES}
