import 'maplibre-gl/dist/maplibre-gl.css';

import type * as GeoJSON from 'geojson';
import type { ExpressionSpecification, MapLayerMouseEvent } from 'maplibre-gl';
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from 'react';
import { useTranslation } from 'react-i18next';

import i18n from '@/i18n';
import * as maplibregl from '@/lib/maplibre';
import type { CorpusBody, CorpusJurisdiction } from '@/lib/types';
import { loadWorldCountries } from '@/lib/world-geojson';

import { TIER_DESCRIPTION, TIER_LABEL } from './tier-badge';

interface WorldMapProps {
  jurisdictions: CorpusJurisdiction[];
  bodies: CorpusBody[];
  selectedBody: string;
  highlightSet?: Set<string>;
  onSelect: (code: string) => void;
  webglFallbackHint?: string | null;
}

interface HoverState {
  name: string;
  stele_code: string;
  tier: number;
  x: number;
  y: number;
}

function oklchToRgb(l: number, c: number, h: number): string {
  const hRad = (h * Math.PI) / 180;
  const a = c * Math.cos(hRad);
  const b = c * Math.sin(hRad);

  const l_ = l + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = l - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = l - 0.0894841775 * a - 1.291485548 * b;

  const l3 = l_ * l_ * l_;
  const m3 = m_ * m_ * m_;
  const s3 = s_ * s_ * s_;

  let r = 4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3;
  let g = -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3;
  let b2 = -0.0041960863 * l3 - 0.7034186147 * m3 + 1.707614701 * s3;

  const toSrgb = (u: number) => (u <= 0.0031308 ? 12.92 * u : 1.055 * u ** (1 / 2.4) - 0.055);
  r = Math.max(0, Math.min(1, toSrgb(r)));
  g = Math.max(0, Math.min(1, toSrgb(g)));
  b2 = Math.max(0, Math.min(1, toSrgb(b2)));

  return `rgb(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b2 * 255)})`;
}

function parseOklch(value: string): [number, number, number] | null {
  const match = value.match(/oklch\(\s*([\d.]+)(%?)\s+([\d.]+)\s+([\d.]+)/i);
  if (!match) return null;
  let l = parseFloat(match[1] ?? '');
  if (match[2] === '%') l /= 100;
  const c = parseFloat(match[3] ?? '');
  const h = parseFloat(match[4] ?? '');
  if (!Number.isFinite(l) || !Number.isFinite(c) || !Number.isFinite(h)) return null;
  return [l, c, h];
}

function resolveCssColor(varName: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback;
  const raw = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
  if (!raw) return fallback;
  const parsed = parseOklch(raw);
  if (parsed) return oklchToRgb(parsed[0], parsed[1], parsed[2]);
  if (/^(rgb|hsl|#)/i.test(raw)) return raw;
  return fallback;
}

const TIER_PALETTE: Record<number, [number, number, number]> = {
  0: [0.94, 0.005, 250],
  1: [0.34, 0.13, 250],
  2: [0.52, 0.11, 250],
  3: [0.72, 0.08, 250],
  4: [0.84, 0.05, 250],
  5: [0.62, 0.008, 250],
};

function readTokens(): Record<number, string> {
  const out: Record<number, string> = {};
  for (const [tier, [l, c, h]] of Object.entries(TIER_PALETTE)) {
    out[Number(tier)] = oklchToRgb(l, c, h);
  }
  return out;
}

const TIER_ORDER = [1, 2, 3, 4, 5] as const;

const TOOLTIP_MAX_WIDTH = 260;
const TOOLTIP_EDGE_BUFFER = 16;
const TOOLTIP_POSITION_CLASS =
  // rtl-codemod-ignore-next-line: physical origin pairs with a pixel-space translate(), no RTL flip
  'pointer-events-none absolute left-0 top-0 rounded-md border border-ink-20 bg-paper px-3 py-2 text-xs shadow-popover-whisper';

function swatchStyle(color: string): React.CSSProperties {
  return { backgroundColor: color };
}

function buildFillExpression(tokenMap: Record<number, string>): ExpressionSpecification {
  return [
    'case',
    ['==', ['get', 'tier'], 1],
    tokenMap[1] ?? 'rgb(155, 198, 191)',
    ['==', ['get', 'tier'], 2],
    tokenMap[2] ?? 'rgb(212, 192, 130)',
    ['==', ['get', 'tier'], 3],
    tokenMap[3] ?? 'rgb(247, 210, 200)',
    ['==', ['get', 'tier'], 4],
    tokenMap[4] ?? 'rgb(229, 128, 105)',
    ['==', ['get', 'tier'], 5],
    tokenMap[5] ?? 'rgb(120, 125, 134)',
    tokenMap[0] ?? 'rgb(238, 240, 244)',
  ];
}

interface TooltipHandle {
  show: (state: HoverState) => void;
  hide: () => void;
}

export function WorldMap({
  jurisdictions,
  bodies,
  selectedBody,
  highlightSet,
  onSelect,
  webglFallbackHint,
}: WorldMapProps) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const tooltipRef = useRef<TooltipHandle>(null);
  const onSelectRef = useRef(onSelect);
  useEffect(() => {
    onSelectRef.current = onSelect;
  });
  const featureCodesRef = useRef<Array<{ id: string | number; code: string }>>([]);

  const [ready, setReady] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [tokens, setTokens] = useState<Record<number, string>>(() => readTokens());

  const byCode = useMemo(() => {
    const map = new Map<string, CorpusJurisdiction>();
    for (const j of jurisdictions) map.set(j.code, j);
    return map;
  }, [jurisdictions]);

  const tierCounts = useMemo(() => {
    const counts: Record<number, number> = { 0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 };
    for (const j of jurisdictions) {
      const t = j.effective_tier ?? j.tier ?? 0;
      counts[t] = (counts[t] ?? 0) + 1;
    }
    return counts;
  }, [jurisdictions]);

  const [webglError, setWebglError] = useState(() => {
    const probe = document.createElement('canvas');
    const gl = probe.getContext('webgl2');
    gl?.getExtension('WEBGL_lose_context')?.loseContext();
    return !gl;
  });

  useEffect(() => {
    const container = containerRef.current;
    if (!container || mapRef.current || webglError) return;

    const tokenMap = readTokens();
    setTokens(tokenMap);

    const buildMap = (): maplibregl.Map =>
      new maplibregl.Map({
        container,
        style: {
          version: 8,
          sources: {},
          layers: [
            {
              id: 'background',
              type: 'background',
              paint: {
                'background-color': tokenMap[0] ?? 'rgb(238, 240, 244)',
                'background-opacity': 0.4,
              },
            },
          ],
          glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
        },
        center: [15, 20],
        zoom: 1.3,
        minZoom: 1,
        maxZoom: 6,
        attributionControl: false,
        dragRotate: false,
        pitchWithRotate: false,
        renderWorldCopies: false,
        keyboard: false,
      });

    let map: maplibregl.Map;
    try {
      map = buildMap();
    } catch {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- exceptional exit: runs once, only when the GL context died between probe and construction
      setWebglError(true);
      return;
    }

    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    map.addControl(
      new maplibregl.AttributionControl({
        compact: true,
        customAttribution: 'Natural Earth · Stele',
      }),
    );

    map.on('load', async () => {
      try {
        const data = await loadWorldCountries();

        map.addSource('countries', {
          type: 'geojson',
          data,
        });

        featureCodesRef.current = (data.features ?? [])
          .map((f: GeoJSON.Feature) => ({
            id: (f.id ?? '') as string | number,
            code: String((f.properties ?? {}).stele_code ?? ''),
          }))
          .filter((x: { id: string | number; code: string }) => x.id !== '');

        const fillExpr = buildFillExpression(tokenMap);

        map.addLayer({
          id: 'countries-fill',
          type: 'fill',
          source: 'countries',
          paint: {
            'fill-color': fillExpr,
            'fill-opacity': [
              'case',
              ['boolean', ['feature-state', 'dimmed'], false],
              0.2,
              ['boolean', ['feature-state', 'hover'], false],
              1,
              ['==', ['get', 'tier'], 0],
              0.35,
              0.85,
            ] as unknown as ExpressionSpecification,
          },
        });

        const bg = resolveCssColor('--background', 'rgb(255, 255, 255)');
        map.addLayer({
          id: 'countries-outline',
          type: 'line',
          source: 'countries',
          paint: {
            'line-color': bg,
            'line-width': 0.4,
            'line-opacity': 0.9,
          },
        });

        const primary = resolveCssColor('--primary', 'rgb(26, 39, 68)');
        map.addLayer({
          id: 'countries-member-outline',
          type: 'line',
          source: 'countries',
          paint: {
            'line-color': primary,
            'line-width': [
              'case',
              ['boolean', ['feature-state', 'member'], false],
              1.8,
              0,
            ] as unknown as ExpressionSpecification,
            'line-opacity': 0.95,
          },
        });

        let hoveredId: string | number | null = null;
        map.on('mousemove', 'countries-fill', (e: MapLayerMouseEvent) => {
          const feature = (e.features ?? [])[0];
          if (!feature) return;
          const fid = feature.id as string | number | undefined;
          if (fid !== undefined && fid !== null) {
            if (hoveredId !== null && hoveredId !== fid) {
              map.setFeatureState({ source: 'countries', id: hoveredId }, { hover: false });
            }
            hoveredId = fid;
            map.setFeatureState({ source: 'countries', id: fid }, { hover: true });
          }
          const props = feature.properties ?? {};
          map.getCanvas().style.cursor = props.stele_code ? 'pointer' : 'default';
          const point = map.project(e.lngLat);
          tooltipRef.current?.show({
            name: String(props.name ?? ''),
            stele_code: String(props.stele_code ?? ''),
            tier: Number(props.tier ?? 0),
            x: point.x,
            y: point.y,
          });
        });

        map.on('mouseleave', 'countries-fill', () => {
          if (hoveredId !== null) {
            map.setFeatureState({ source: 'countries', id: hoveredId }, { hover: false });
            hoveredId = null;
          }
          map.getCanvas().style.cursor = '';
          tooltipRef.current?.hide();
        });

        map.on('click', 'countries-fill', (e: MapLayerMouseEvent) => {
          const feature = (e.features ?? [])[0];
          if (!feature) return;
          const code = String((feature.properties ?? {}).stele_code ?? '');
          if (code) onSelectRef.current(code);
        });

        map.getCanvas().removeAttribute('tabindex');

        setReady(true);
      } catch (err) {
        console.error('Failed to load world GeoJSON', err);
        setLoadError(err instanceof Error ? err.message : i18n.t('Map data failed to load'));
      }
    });

    mapRef.current = map;

    const applyTheme = () => {
      if (!map.isStyleLoaded()) return;
      const next = readTokens();
      setTokens(next);
      if (map.getLayer('background')) {
        map.setPaintProperty('background', 'background-color', next[0] ?? '#eef2f7');
      }
      if (map.getLayer('countries-fill')) {
        map.setPaintProperty('countries-fill', 'fill-color', buildFillExpression(next));
      }
      if (map.getLayer('countries-outline')) {
        map.setPaintProperty(
          'countries-outline',
          'line-color',
          resolveCssColor('--background', 'rgb(255, 255, 255)'),
        );
      }
      if (map.getLayer('countries-member-outline')) {
        map.setPaintProperty(
          'countries-member-outline',
          'line-color',
          resolveCssColor('--primary', 'rgb(26, 39, 68)'),
        );
      }
    };
    const themeObserver = new MutationObserver(applyTheme);
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class', 'data-theme', 'style'],
    });

    return () => {
      themeObserver.disconnect();
      map.remove();
      mapRef.current = null;
    };
  }, [webglError]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;

    if (selectedBody) {
      const body = bodies.find((b) => b.code === selectedBody);
      const memberCodes = new Set(body?.members ?? []);
      let minLat = Infinity,
        maxLat = -Infinity,
        minLng = Infinity,
        maxLng = -Infinity;
      for (const j of jurisdictions) {
        if (memberCodes.has(j.code) && j.coordinates) {
          const { lat, lng } = j.coordinates;
          if (lat < minLat) minLat = lat;
          if (lat > maxLat) maxLat = lat;
          if (lng < minLng) minLng = lng;
          if (lng > maxLng) maxLng = lng;
        }
      }
      if (Number.isFinite(minLat)) {
        map.fitBounds(
          [
            [minLng, minLat],
            [maxLng, maxLat],
          ],
          { padding: 120, maxZoom: 3.5, duration: 1200, essential: true },
        );
      }
    } else {
      map.flyTo({
        center: [15, 20],
        zoom: 1.3,
        duration: 800,
        essential: true,
      });
    }
  }, [selectedBody, bodies, jurisdictions, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;

    const memberSet = new Set<string>();
    if (selectedBody) {
      const body = bodies.find((b) => b.code === selectedBody);
      if (body) for (const m of body.members) memberSet.add(m);
    }
    const dimActive = !!highlightSet;

    for (const { id, code } of featureCodesRef.current) {
      map.setFeatureState(
        { source: 'countries', id },
        {
          dimmed: dimActive && !highlightSet!.has(code),
          member: selectedBody !== '' && memberSet.has(code),
        },
      );
    }
  }, [selectedBody, bodies, highlightSet, ready]);

  return (
    <div className="space-y-4">
      <div className="relative border-y border-border/50 bg-muted/20">
        <div
          ref={containerRef}
          role={webglError ? undefined : 'img'}
          aria-hidden={webglError || undefined}
          aria-label={
            webglError
              ? undefined
              : t('Interactive jurisdiction map. Use the jurisdiction table to select by keyboard.')
          }
          className="w-full h-[clamp(320px,60vh,640px)]"
        />

        {webglError && (
          <div
            role="alert"
            className="absolute inset-0 flex items-center justify-center bg-paper/90"
          >
            <div className="max-w-sm rounded-md border border-ink-20 bg-background px-4 py-3 text-sm">
              <p className="font-medium">{t('The map view isn’t available in this browser.')}</p>
              {webglFallbackHint !== null ? (
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {webglFallbackHint ??
                    t('It needs WebGL 2. The table below lists every jurisdiction.')}
                </p>
              ) : null}
            </div>
          </div>
        )}

        {loadError && (
          <div
            role="alert"
            className="pointer-events-none absolute inset-0 flex items-center justify-center bg-paper/90"
          >
            <div className="pointer-events-auto max-w-sm rounded-md border border-destructive/30 bg-background px-4 py-3 text-sm">
              <p className="font-medium">{t('Map data didn’t load.')}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {t('{{error}}. Refresh the page to try again.', { error: loadError })}
              </p>
            </div>
          </div>
        )}

        <MapTooltip ref={tooltipRef} byCode={byCode} containerRef={containerRef} />
      </div>

      <div
        className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs"
        aria-label={t('Data quality tier legend')}
      >
        {TIER_ORDER.map((tier) => (
          <span
            key={tier}
            className="flex items-center gap-1.5"
            title={t(TIER_DESCRIPTION[tier] ?? '')}
          >
            <span
              className="inline-block h-2.5 w-2.5 rounded-sm"
              style={swatchStyle(tokens[tier] ?? 'rgb(170, 175, 184)')}
              aria-hidden="true"
            />
            <span className="text-muted-foreground tabular-nums">
              {t(TIER_LABEL[tier] ?? '')} · {tierCounts[tier] ?? 0}
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}

const MapTooltip = forwardRef<
  TooltipHandle,
  {
    byCode: Map<string, CorpusJurisdiction>;
    containerRef: RefObject<HTMLDivElement | null>;
  }
>(function MapTooltip({ byCode, containerRef }, ref) {
  const { t } = useTranslation();
  const [hover, setHover] = useState<HoverState | null>(null);
  const [containerWidth, setContainerWidth] = useState(800);

  useImperativeHandle(
    ref,
    () => ({
      show: (state) => setHover(state),
      hide: () => setHover(null),
    }),
    [],
  );

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const update = () => setContainerWidth(el.clientWidth);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, [containerRef]);

  if (!hover) return null;

  const hoveredJurisdiction = hover.stele_code ? byCode.get(hover.stele_code) : undefined;
  const tierLabel = TIER_LABEL[hover.tier];
  const tierDescription = TIER_DESCRIPTION[hover.tier];
  const left = Math.max(
    TOOLTIP_EDGE_BUFFER / 2,
    Math.min(hover.x + 12, containerWidth - (TOOLTIP_MAX_WIDTH + TOOLTIP_EDGE_BUFFER)),
  );
  const top = Math.max(TOOLTIP_EDGE_BUFFER / 2, hover.y - 56);

  return (
    <div
      className={TOOLTIP_POSITION_CLASS}
      style={{
        maxWidth: `min(${TOOLTIP_MAX_WIDTH}px, calc(100% - ${TOOLTIP_EDGE_BUFFER * 2}px))`,
        transform: `translate(${left}px, ${top}px)`,
        willChange: 'transform',
      }}
    >
      <div className="line-clamp-2 font-medium">{hoveredJurisdiction?.name ?? hover.name}</div>
      <div className="text-muted-foreground">
        {hover.stele_code ? (
          <>
            {tierLabel ? t(tierLabel) : t('Tier {{tier}}', { tier: hover.tier })}
            {hoveredJurisdiction?.continent ? ` · ${hoveredJurisdiction.continent}` : ''}
          </>
        ) : (
          t('No Stele profile yet')
        )}
      </div>
      {hover.stele_code && tierDescription && (
        <div className="mt-1 line-clamp-2 text-[11px] text-ink-60">{t(tierDescription)}</div>
      )}
    </div>
  );
});
