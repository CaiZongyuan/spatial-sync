import { createContext, useEffect, useState } from 'react';

export const defaultViewerTheme = {
  ground: '#121211', floor: '#2a2926', wall: '#363531', body: '#9a978f', steel: '#8c8b85',
  glass: '#aabdcc', edge: '#c9c5bb', grid: '#46443f', accent: '#9ca4ff', sky: '#eef0ff',
  sun: '#fff4e4', hemi: .95, key: 1.05, ao: .66, edgeOpacity: .32,
};
export type ViewerTheme = typeof defaultViewerTheme;
export const ViewerThemeContext = createContext(defaultViewerTheme);

function readTheme(): ViewerTheme {
  const css = getComputedStyle(document.documentElement);
  const color = (token: string, fallback: string) => css.getPropertyValue(token).trim() || fallback;
  const number = (token: string, fallback: number) => {
    const raw = css.getPropertyValue(token).trim();
    const parsed = Number(raw);
    return raw && Number.isFinite(parsed) ? parsed : fallback;
  };
  return {
    ground: color('--ground', defaultViewerTheme.ground),
    floor: color('--m-floor', defaultViewerTheme.floor), wall: color('--m-wall', defaultViewerTheme.wall),
    body: color('--m-body', defaultViewerTheme.body), steel: color('--m-steel', defaultViewerTheme.steel),
    glass: color('--m-glass', defaultViewerTheme.glass), edge: color('--m-edge', defaultViewerTheme.edge),
    grid: color('--m-grid', defaultViewerTheme.grid), accent: color('--accent', defaultViewerTheme.accent),
    sky: color('--m-sky', defaultViewerTheme.sky), sun: color('--m-sun', defaultViewerTheme.sun),
    hemi: number('--m-hemi', .95), key: number('--m-key', 1.05), ao: number('--m-ao', .66),
    edgeOpacity: number('--m-edge-o', .32),
  };
}

export function useViewerTheme() {
  const [theme, setTheme] = useState(readTheme);
  useEffect(() => {
    const update = () => setTheme(readTheme());
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'style', 'class'] });
    const media = matchMedia('(prefers-color-scheme: light)');
    media.addEventListener('change', update);
    update();
    return () => { observer.disconnect(); media.removeEventListener('change', update); };
  }, []);
  return theme;
}
