import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)

// Фотографий 58 тысяч и весят они 12.5 ГБ — копировать их в public/ нельзя.
// Вместо этого дев-сервер отдаёт их прямо из папок выгрузки Telegram.
// Список каналов один на весь проект и лежит в tools/sources.py; здесь
// продублированы только папки, чтобы конфиг не зависел от Python.
const PHOTO_DIRS = {
  arkh: path.resolve(__dirname, '..', 'Archi', 'photos'),
  swamps: path.resolve(__dirname, '..', 'Swaps_and_churches', 'photos'),
  ozzed: path.resolve(__dirname, '..', 'ozzed expeditions', 'photos'),
}

// Сюда tools/build_photos.py складывает уменьшенные версии. В деве они
// не обязательны: если нужного размера нет, отдаём исходник.
const RESIZED = path.resolve(__dirname, '..', 'photos-web')

function servePhotos() {
  return {
    name: 'serve-telegram-photos',
    configureServer(server) {
      // адрес вида /photos/<ширина>/<канал>/<файл>
      server.middlewares.use('/photos', (req, res, next) => {
        const parts = decodeURIComponent(req.url.split('?')[0])
          .replace(/^\//, '')
          .split('/')
        const [size, src, ...rest] = parts
        const name = rest.join('/')
        // имя файла — только имя: никаких «..» и вложенных путей,
        // иначе адресом можно вытащить что угодно с диска
        if (!/^\d+$/.test(size) || !PHOTO_DIRS[src]) return next()
        if (!name || name !== path.basename(name)) return next()

        // сначала ищем уменьшенную версию, потом исходник
        const file = [
          path.join(RESIZED, size, src, name),
          path.join(PHOTO_DIRS[src], name),
        ].find((f) => fs.existsSync(f))
        if (!file) return next()

        res.setHeader('Content-Type', 'image/jpeg')
        res.setHeader('Cache-Control', 'max-age=86400')
        fs.createReadStream(file).pipe(res)
      })
    },
  }
}

// MapLibre 6 вычисляет адрес своего воркера в рантайме:
//   new URL('maplibre-gl-worker.mjs', import.meta.url)
// Бандлер такой путь не видит и файл рядом с бандлом не кладёт. Воркер
// молча не запускается — карта остаётся пустой, причём без единой ошибки
// в консоли, потому что падает загрузка модуля уже внутри воркера.
// В деве лечится отказом от пребандлинга, в сборке — копированием файлов
// воркера в ту же папку, где лежит итоговый чанк.
const MAPLIBRE_RUNTIME_FILES = ['maplibre-gl-worker.mjs', 'maplibre-gl-shared.mjs']

function maplibreWorker() {
  return {
    name: 'maplibre-worker-files',
    apply: 'build',
    generateBundle() {
      const dir = path.dirname(require.resolve('maplibre-gl/dist/maplibre-gl.mjs'))
      for (const name of MAPLIBRE_RUNTIME_FILES) {
        this.emitFile({
          type: 'asset',
          fileName: `assets/${name}`,
          source: fs.readFileSync(path.join(dir, name)),
        })
      }
    },
  }
}

export default defineConfig({
  plugins: [react(), servePhotos(), maplibreWorker()],
  server: {
    // правки и предложения обслуживает отдельный процесс (server/index.js),
    // фронтенду он виден как /api на том же адресе
    // подложка карты — через тот же путь, что на стенде (nginx, /ofm/ и
    // /osm/): из России без VPN напрямую она не грузится
    proxy: {
      '/api': 'http://localhost:5174',
      '/ofm': { target: 'https://tiles.openfreemap.org', changeOrigin: true, rewrite: (p) => p.replace(/^\/ofm/, '') },
      '/osm': {
        target: 'https://tile.openstreetmap.org',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/osm/, ''),
        headers: { 'User-Agent': 'archimap-dev/1.0' },
      },
    },
  },
  optimizeDeps: {
    exclude: ['maplibre-gl'],
  },
})
