const paths = {
  overview: 'M3 3h6v6H3z M15 3h6v6h-6z M3 15h6v6H3z M15 15h6v6h-6z',
  live: 'M3 12h4l3-7 4 14 3-7h4',
  resources: 'M5 6h14v12H5z M9 2v4 M15 2v4 M9 18v4 M15 18v4 M1 10h4 M1 14h4 M19 10h4 M19 14h4 M9 10h6v4H9z',
  sessions: 'M4 4h16v16H4z M4 9h16 M9 9v11',
  scenes: 'M12 3l9 5-9 5-9-5z M3 8v9l9 5 9-5V8 M12 13v9',
  plans: 'M10 5h11 M10 12h11 M10 19h11 M3 5l2 2 3-4 M3 12l2 2 3-4 M3 19h4',
  training: 'M4 20h16 M6 16v-5 M12 16V4 M18 16V8 M3 4h4',
  evaluation: 'M4 20h16 M6 16v-5 M12 16V4 M18 16V8 M3 4h4 M5 2v4',
  cost: 'M4 20h16 M6 16v-5 M12 16V7 M18 16V3 M3 4h6 M6 1v6',
  lock: 'M6 10h12v11H6z M8 10V6a4 4 0 0 1 8 0v4 M12 14v3',
  assets: 'M12 3l9 5v9l-9 5-9-5V8z M3 8l9 5 9-5 M12 13v9',
  recordings: 'M4 4h16v16H4z M10 8l6 4-6 4z',
  approvals: 'M8 4h12v17H4V4h4 M8 2h8v4H8z M8 13l3 3 6-6',
  collapse: 'M14 6l-6 6 6 6',
  chevron: 'M9 5l7 7-7 7',
  group: 'M3 7h7l2-3h9v16H3z',
  device: 'M5 3h14v18H5z M8 7h8 M8 11h5 M8 16h2 M14 16h2',
  body: 'M12 3l8 4.5v9L12 21l-8-4.5v-9z M4 7.5l8 4.5 8-4.5 M12 12v9',
  static: 'M4 5h16v12H4z M2 21h20 M7 17l-3 4 M13 17l-3 4 M19 17l-3 4',
  site: 'M12 2v5 M12 17v5 M2 12h5 M17 12h5 M8 12a4 4 0 1 0 8 0a4 4 0 1 0-8 0',
  joint: 'M3 7h6 M15 17h6 M8 7l8 10 M6 7a2 2 0 1 0 4 0a2 2 0 1 0-4 0 M14 17a2 2 0 1 0 4 0a2 2 0 1 0-4 0',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z M9 12a3 3 0 1 0 6 0a3 3 0 1 0-6 0',
  eyeOff: 'M3 3l18 18 M10 5a13 13 0 0 1 12 7s-2 4-5 6 M6 6a20 20 0 0 0-4 6s4 7 10 7h2 M9 9a4 4 0 0 0 6 6',
  search: 'M10 3a7 7 0 1 0 0 14a7 7 0 1 0 0-14 M15 15l6 6',
  play: 'M7 4l13 8-13 8z',
  pause: 'M7 4v16 M17 4v16',
  step: 'M5 4l12 8-12 8z M20 4v16',
  reset: 'M4 10a8 8 0 1 1 1 8 M4 4v6h6',
  reload: 'M20 10a8 8 0 0 0-14-5L3 8 M3 3v5h5 M4 14a8 8 0 0 0 14 5l3-3 M16 16h5v5',
  close: 'M6 6l12 12 M18 6L6 18',
  check: 'M4 12l5 5L20 6',
  circle: 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18',
  warning: 'M12 3l10 18H2z M12 9v5 M12 17h.01',
  copy: 'M8 8h13v13H8z M16 8V3H3v13h5',
  external: 'M14 3h7v7 M21 3L10 14 M10 4H3v17h17v-7',
  fullscreen: 'M8 3H3v5 M16 3h5v5 M21 16v5h-5 M8 21H3v-5',
  exitFullscreen: 'M3 8h5V3 M21 8h-5V3 M16 21v-5h5 M8 21v-5H3',
  theme: 'M12 2a10 10 0 1 0 0 20V2z M12 2a10 10 0 0 1 0 20',
  list: 'M8 5h13 M8 12h13 M8 19h13 M3 5h.01 M3 12h.01 M3 19h.01',
} as const;

export type IconName = keyof typeof paths;

export function Icon({ name, className = 'nav-icon' }: { name: IconName; className?: string }) {
  return <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
