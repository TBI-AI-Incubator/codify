let geojsonPromise: Promise<GeoJSON.FeatureCollection> | null = null;

export function loadWorldCountries(): Promise<GeoJSON.FeatureCollection> {
  if (!geojsonPromise) {
    geojsonPromise = fetch('/world-countries.geojson')
      .then((res) => {
        if (!res.ok) throw new Error(`GeoJSON ${res.status}`);
        return res.json() as Promise<GeoJSON.FeatureCollection>;
      })
      .catch((err) => {
        geojsonPromise = null;
        throw err;
      });
  }
  return geojsonPromise;
}
