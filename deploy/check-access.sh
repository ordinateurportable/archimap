#!/usr/bin/env bash
# Почему стенд не открывается через VPN: пакеты до сервера доходят
# (tracert из-под VPN 30.09 получил ответ от сервера), а подключения
# к 443 и 22 — нет. Смотрим фильтры на самом сервере. Запускать без VPN:
#   bash deploy/check-access.sh [адрес VPN]
# Адрес VPN по умолчанию — ARCHIMAP_VPN_IP из deploy/target.env.
# Куда выкладывать — в deploy/target.env (в git не идёт, образец —
# deploy/target.env.example): адрес сервера и ключ SSH не публикуем
. "$(dirname "$0")/target.env" 2>/dev/null || { echo "нет deploy/target.env — скопируйте deploy/target.env.example и впишите свой сервер"; exit 1; }
HOST=$ARCHIMAP_HOST
KEY=$ARCHIMAP_KEY
VPN_IP="${1:-${ARCHIMAP_VPN_IP:-}}"

ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=20 "$HOST" VPN_IP="$VPN_IP" 'bash -s' <<'SH'
echo "== ufw";        (ufw status verbose 2>/dev/null || echo "   ufw нет") | head -30
echo "== fail2ban";   (fail2ban-client status 2>/dev/null || echo "   fail2ban нет") | head -10
for j in $(fail2ban-client status 2>/dev/null | sed -n 's/.*Jail list:\s*//p' | tr ',' ' '); do
  echo "   $j: $(fail2ban-client status "$j" | grep -E 'Currently banned|Total banned' | tr -s ' \t' ' ' | tr '\n' ' ')"
done
echo "== адрес VPN ($VPN_IP) в правилах"
(iptables -S 2>/dev/null; nft list ruleset 2>/dev/null; fail2ban-client banned 2>/dev/null) | grep -F "$VPN_IP" || echo "   не найден"
echo "== iptables INPUT (первые правила)"
iptables -S INPUT 2>/dev/null | head -25
echo "== nftables: есть ли таблицы"
nft list tables 2>/dev/null || echo "   nft нет"
echo "== кто слушает 80/443/22"
ss -tlnp | grep -E ':(80|443|22) ' | awk '{print "   " $4, $6}'
echo "== последние отказы и баны в журнале"
journalctl -u fail2ban --since "7 days ago" --no-pager 2>/dev/null | grep -iE "ban" | tail -5
SH
