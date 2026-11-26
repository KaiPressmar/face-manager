# Offline world map data

The bundled `world-land.json` retains polygon rings as longitude/latitude pairs,
rounded to three decimal places. [Natural Earth v5.1.2 land at 1:110m](https://github.com/nvkelso/natural-earth-vector/blob/v5.1.2/geojson/ne_110m_land.geojson)

The bundled `world-borders.json` uses the same precision and stores each line
separately. It groups international boundaries separately from other boundary and
control lines for dashed rendering. [Natural Earth v5.1.2 land boundaries at 1:110m](https://github.com/nvkelso/natural-earth-vector/blob/v5.1.2/geojson/ne_110m_admin_0_boundary_lines_land.geojson)

`world-countries.json` contains German country labels, alternate names,
label anchors and approximate zoom extents from the largest ring of each
country geometry. [Natural Earth v5.1.2 countries at 1:110m](https://github.com/nvkelso/natural-earth-vector/blob/v5.1.2/geojson/ne_110m_admin_0_countries.geojson)

`world-detail.json` contains land, border, lake and river geometry simplified
at 0.015 degrees and projected to the map's equirectangular SVG. It also includes
German names and coordinates for capitals and cities with at least 150,000
people in the source data. [Natural Earth v5.1.2 land](https://github.com/nvkelso/natural-earth-vector/blob/v5.1.2/geojson/ne_50m_land.geojson) · [Land boundaries](https://github.com/nvkelso/natural-earth-vector/blob/v5.1.2/geojson/ne_50m_admin_0_boundary_lines_land.geojson) · [Lakes](https://github.com/nvkelso/natural-earth-vector/blob/v5.1.2/geojson/ne_50m_lakes.geojson) · [Rivers](https://github.com/nvkelso/natural-earth-vector/blob/v5.1.2/geojson/ne_50m_rivers_lake_centerlines.geojson) · [Populated places](https://github.com/nvkelso/natural-earth-vector/blob/v5.1.2/geojson/ne_50m_populated_places.geojson)

`world-water-overview.json` contains the widest lakes and major rivers from
the same source for use in the overview map.

`public/map/world-relief.webp` downsamples the equirectangular Natural Earth
1:50m shaded relief and adjusts the grayscale range for blending over land.
[Natural Earth shaded relief](https://www.naturalearthdata.com/downloads/50m-raster-data/50m-shaded-relief/)

The journey place index keeps populated-place points, sorted by latitude, with
four decimal places. It uses German names when available and falls back to the
primary Natural Earth name. Historic places and scientific and meteorological
stations are omitted. [Natural Earth 1:10m populated places v5.1.2](https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-populated-places/)

These Natural Earth datasets ship with the app for offline use. [Natural Earth public-domain terms](https://www.naturalearthdata.com/about/terms-of-use/)

`public/map/world-satellite.webp` contains the August 2004 global Blue Marble
Next Generation composite with topography and bathymetry. The original NASA
Earth Observatory JPEG was converted to WebP without cropping or changing its
5400 × 2700 dimensions. The equirectangular raster covers longitude −180° to
180° and latitude 90° to −90°. At this bundled resolution it is an overview,
with about 7.4 km per pixel at the equator. It is embedded locally in exports
with NASA attribution. [NASA Earth Observatory dataset and credits](https://science.nasa.gov/earth/earth-observatory/blue-marble-next-generation/) · [Original raster](https://assets.science.nasa.gov/dynamicimage/assets/science/esd/eo/images/bmng/bmng-topography-bathymetry/august/world.topo.bathy.200408.3x5400x2700.jpg?w=5400&h=2700&fit=clip)

NASA Earth Observatory credits the dataset to Reto Stöckli (NASA Goddard
Space Flight Center). NASA describes its imagery as generally exempt from
U.S. copyright and permits informational use, including photo collections,
with source acknowledgment. NASA does not endorse Face Manager.
[NASA media usage guidelines](https://www.nasa.gov/nasa-brand-center/images-and-media/)
