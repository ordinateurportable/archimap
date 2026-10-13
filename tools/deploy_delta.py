# -*- coding: utf-8 -*-
"""Предложения модели для выкладки — только новые и изменённые.

    python tools/deploy_delta.py                       # сколько новых и изменённых
    python tools/deploy_delta.py stage/llm/suggestions.json   # записать разницу

Отправленное на стенд лежит в data/llm/deploy/sent-*.json (по копии на каждую
выкладку). Полный suggestions.json слать нельзя: post-suggestions заменяет
предложение по ключу, и удалённые модератором на стенде вернулись бы. После
выкладки скопируйте записанный файл в data/llm/deploy/sent-<дата>.json.
"""
import glob, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LLM = os.path.join(ROOT, "data", "llm")


def same(a, b):
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def main():
    sent = {}
    for f in sorted(glob.glob(os.path.join(LLM, "deploy", "sent-*.json"))):
        for s in json.load(open(f, encoding="utf-8")):
            sent[s["key"]] = s
    cur = json.load(open(os.path.join(LLM, "suggestions.json"), encoding="utf-8"))
    new = [s for s in cur if s["key"] not in sent]
    changed = [s for s in cur if s["key"] in sent and not same(s, sent[s["key"]])]
    print(f"всего {len(cur)}, отправлено раньше {len(sent)}, новых {len(new)}, изменённых {len(changed)}")
    if len(sys.argv) > 1:
        json.dump(new + changed, open(sys.argv[1], "w", encoding="utf-8", newline="\n"), ensure_ascii=False)
        print("записано", sys.argv[1])


if __name__ == "__main__":
    main()
