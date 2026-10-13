# Выкладка на сервер

Проверено на Ubuntu 24.04 с диском 10 ГБ. Имя сайта — через
[sslip.io](https://sslip.io): это бесплатный DNS, который отдаёт адрес
прямо из имени, так что домен покупать не нужно.

Команды с пометкой **[локально]** выполняются в Git Bash в папке проекта,
с пометкой **[сервер]** — по SSH.

## Сколько нужно места

Российская версия в профиле «превью 400px для всех фотографий + 800px
для первого фото поста» — **3.07 ГБ**, 49 377 файлов. Плюс сайт (27 МБ),
Node и nginx. На диске 10 ГБ с чистой Ubuntu (2.3 ГБ) это помещается
с запасом около 3.5 ГБ.

Если места меньше, профиль полегче — качество в карточках и на карте
не изменится, мельче станут только фотографии, открытые в окне поста:

```bash
python tools/build_photos.py --group ru --large 0   # ~1.2 ГБ
```

---

## 0. Имя сайта

sslip.io отдаёт адрес прямо из имени, поэтому домен не нужен. Узнать IP
сервера — **[сервер]** `curl -4 ifconfig.me`.

Важно: sslip.io понимает и поддомены, `что-угодно.<IP>.sslip.io` ведёт
на тот же адрес. Это и нужно, когда на сервере уже живёт другой проект:
пусть он остаётся на `<IP>.sslip.io`, а карта переедет на отдельное имя.

В этой инструкции используется `archimap.<IP сервера>.sslip.io` —
подставьте свой адрес.

Чтобы не набирать пароль при каждой заливке, можно положить на сервер
свой ключ. **[локально]**, если ключа ещё нет:

```bash
ssh-keygen -t ed25519            # на все вопросы можно нажать Enter
cat ~/.ssh/id_ed25519.pub | ssh root@<IP сервера> \
  "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

## 1. Посмотреть, что уже стоит

**[сервер]**

```bash
nginx -v; node -v                       # что уже есть
ls -la /etc/nginx/sites-enabled/        # какие сайты уже настроены
grep -r server_name /etc/nginx/sites-enabled/
```

Ставим только то, чего не хватает:

```bash
apt update
apt install -y nginx                    # если nginx не ответил версией
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt install -y nodejs                   # если node не ответил версией
```

## 2. Создать папки

**[сервер]**

```bash
mkdir -p /srv/archimap/{web,photos-web,data,server}
```

## 3. Собрать и залить сайт

**[локально]**

```bash
cd web && npm run build && gzip -9 -kf dist/data/*.json && cd ..
```

`gzip` кладёт рядом сжатые копии: nginx отдаст `ru.json.gz` на 4 МБ
вместо 14, не тратя процессор на сжатие при каждом запросе.

Заливаем сайт и сервер правок (это быстро, около 30 МБ):

```bash
tar cf - -C web/dist . | ssh root@<IP сервера> "tar xf - -C /srv/archimap/web"
tar cf - --exclude node_modules -C server . | ssh root@<IP сервера> "tar xf - -C /srv/archimap/server"
tar cf - -C deploy . | ssh root@<IP сервера> "mkdir -p /srv/archimap/deploy && tar xf - -C /srv/archimap/deploy"
```

Третья команда кладёт на сервер образцы конфигов — они понадобятся
на шагах 5 и 6.

## 4. Залить фотографии

Это 3 ГБ, поэтому двумя кусками: если связь оборвётся, переделывать
придётся только один. Фотографии уже в JPEG, сжимать их ещё раз незачем —
`tar` идёт без `-z`.

**[локально]**

```bash
tar cf - -C photos-web 400 | ssh root@<IP сервера> "tar xf - -C /srv/archimap/photos-web"
tar cf - -C photos-web 800 | ssh root@<IP сервера> "tar xf - -C /srv/archimap/photos-web"
```

Первый кусок — 1.3 ГБ, второй — 1.8 ГБ. Прогресса не видно, это нормально;
проверить можно из другого окна **[сервер]**:

```bash
du -sh /srv/archimap/photos-web
```

Когда закончится, должно быть около 3.0 ГБ и 49 377 файлов:

```bash
find /srv/archimap/photos-web -type f | wc -l
```

## 5. Настроить и запустить сервер правок

**[сервер]**

```bash
cd /srv/archimap/server
npm install --omit=dev
cp .env.example .env
nano .env
```

В `.env` обязательно:

```
ADMIN_PASSWORD=свой-пароль-не-dev
TRUST_PROXY=1
```

`TRUST_PROXY=1` нужен потому, что запросы приходят через nginx. Без него
все посетители будут видны сервером под одним адресом и попадут в общий
счётчик ограничений: десять чужих неудачных попыток входа заблокируют
вход всем.

Права на папку с данными и запуск:

```bash
chown -R www-data /srv/archimap/data
# служба работает от www-data, значит .env должен быть ей доступен:
# с правами 600 у root Node скажет «.env not found» и не стартует
chown root:www-data /srv/archimap/server/.env && chmod 640 /srv/archimap/server/.env
cp /srv/archimap/deploy/archimap-api.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now archimap-api
systemctl status archimap-api --no-pager
```

Должно быть `active (running)`. Если нет — `journalctl -u archimap-api -n 30`
покажет причину; чаще всего это пароль `dev` или пустой `ADMIN_PASSWORD`.

## 6. Настроить nginx

**[сервер]** Копируем образец и подставляем в него своё имя:

```bash
cp /srv/archimap/deploy/nginx.conf /etc/nginx/sites-available/archimap
sed -i "s/ДОМЕН/archimap.<IP сервера>.sslip.io/" /etc/nginx/sites-available/archimap
grep server_name /etc/nginx/sites-available/archimap    # проверить, что подставилось
```

Включаем и проверяем:

```bash
ln -sf /etc/nginx/sites-available/archimap /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

**Ничего не удаляйте из `sites-enabled`.** Если на сервере уже есть
другой сайт, он может обслуживаться как раз конфигом `default` — удалив
его, вы положите соседний проект. Наш конфиг работает рядом: nginx
разводит сайты по имени из заголовка `Host`.

Откройте `http://archimap.<IP сервера>.sslip.io` — сайт должен работать.
Дальше можно выдать сертификат.

## 7. HTTPS

**[сервер]**

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d archimap.<IP сервера>.sslip.io --dry-run   # репетиция
certbot --nginx -d archimap.<IP сервера>.sslip.io             # настоящий выпуск
```

Сначала `--dry-run`: у Let's Encrypt есть предел в 5 сертификатов на имя
в неделю, и упереться в него из-за опечатки обидно. Certbot сам допишет
в конфиг `listen 443 ssl`, сертификат и переадресацию с http.

Для http-01 проверки нужен открытый порт 80 — он открыт, у Рег.ру
заблокированы только почтовые 25, 465 и 587.

## Обновление

Пересобрали разбор — обновите только сайт, фотографии заново гонять
не нужно:

```bash
cd web && npm run build && gzip -9 -kf dist/data/*.json && cd ..
tar cf - -C web/dist . | ssh root@<IP сервера> "tar xf - -C /srv/archimap/web"
```

Правки админа лежат в `/srv/archimap/data/edits.json` и обновлением не
затрагиваются. Их стоит иногда забирать себе:

```bash
ssh root@<IP сервера> "cat /srv/archimap/data/edits.json" > data/edits.json
```

## Что проверить после выкладки

- открывается карта, на ней есть зелёные точки;
- в карточках видны фотографии, в окне поста первая открывается крупно;
- вкладка одна, «Россия» — посетитель не видит остальные;
- вход в режим админа по новому паролю, старый `dev` не подходит;
- после правки тега меняется `/srv/archimap/data/edits.json`.

## Если что-то не работает

| симптом | где смотреть |
| ------- | ------------ |
| карта пустая, тайлы есть, ошибок нет | тип `.mjs`: `curl -sI .../assets/maplibre-gl-worker.mjs` должен вернуть `application/javascript` |
| в логе «.env not found», хотя файл есть | права: `ls -la /srv/archimap/server/.env`, нужно `root:www-data` и 640 |
| сайт не открывается | `nginx -t`, `systemctl status nginx` |
| «сервер правок не отвечает» в панели | `journalctl -u archimap-api -n 30` |
| фотографии не грузятся | `ls /srv/archimap/photos-web/400/arkh \| head` |
| вход не принимает пароль | `cat /srv/archimap/server/.env` |
| кончилось место | `df -h /` |
