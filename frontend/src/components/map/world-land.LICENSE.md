# Offline world map data

The bundled `world-land.json` retains polygon rings as longitude/latitude pairs,
rounded to three decimal places. [Natural Earth v5.1.2 land at 1:110m](https://github.com/nvkelso/natural-earth-vector/blob/v5.1.2/geojson/ne_110m_land.geojson)

The bundled `world-borders.json` uses the same precision and stores each line
separately. It groups international boundaries separately from other boundary and
control lines for dashed rendering. [Natural Earth v5.1.2 land boundaries at 1:110m](https://github.com/nvkelso/natural-earth-vector/blob/v5.1.2/geojson/ne_110m_admin_0_boundary_lines_land.geojson)

Both datasets ship with the app for offline use. [Natural Earth public-domain terms](https://www.naturalearthdata.com/about/terms-of-use/)
