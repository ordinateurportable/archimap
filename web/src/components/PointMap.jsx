import { useEffect, useRef } from 'react'
import YandexPicker, { MapSourceToggle, useMapSource } from './YandexPicker.jsx'
import { LngLatBounds, Map as MapLibreMap, Marker, NavigationControl } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { applyStyle, preferredStyle, touchOptions } from '../mapStyle.js'

// Миникарта с точкой — посмотреть, куда попала точка, не уходя из
// модерации. Та же карта, что в «Указать точку вручную», только без правки.
//
// points — [{ lat, lon, color? }]; несколько точек (модель и ручная)
// показываются вместе, чтобы было видно, насколько они расходятся.
export default function PointMap({ points }) {
  const container = useRef(null)
  const mapRef = useRef(null)
  // у админа — та же вторая карта, что в форме точки: Яндекс, спутник
  const { usage, setUsage, yandexOn, choose } = useMapSource({ remember: false })

  useEffect(() => {
    const map = new MapLibreMap({
      container: container.current,
      style: { version: 8, sources: {}, layers: [] }, // настоящий стиль подгрузится ниже
      center: [points[0].lon, points[0].lat],
      zoom: 15,
      attributionControl: { compact: true },
      ...touchOptions(),
    })
    map.addControl(new NavigationControl({ showCompass: false }), 'top-right')
    const unstyle = applyStyle(map, preferredStyle())
    for (const p of points) new Marker({ color: p.color ?? '#ff9500' }).setLngLat([p.lon, p.lat]).addTo(map)
    if (points.length > 1) {
      const bounds = new LngLatBounds()
      points.forEach((p) => bounds.extend([p.lon, p.lat]))
      map.fitBounds(bounds, { padding: 48, maxZoom: 16, duration: 0 })
    }
    mapRef.current = map
    return () => {
      unstyle()
      map.remove()
      mapRef.current = null
    }
    // точки карточки не меняются, пока карта открыта
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // скрытая, пока открыт Яндекс, наша карта не знает размеров — пересчитать
  useEffect(() => {
    if (!yandexOn) mapRef.current?.resize()
  }, [yandexOn])

  // span, а не div: карта раскрывается внутри строки с координатами (<p>)
  return (
    <span className="point-map">
      {usage && <MapSourceToggle usage={usage} yandexOn={yandexOn} onChoose={choose} inline />}
      <span ref={container} className="picker__map point-map" style={yandexOn ? { display: 'none' } : undefined} />
      {yandexOn && <YandexPicker value={points[0]} onChange={() => {}} onUsage={setUsage} readOnly inline />}
    </span>
  )
}
