// Stroke icons (24x24) from the existing app's icon set.
export const ICONS: Record<string, string> = {
  dashboard: "<rect x=\"3\" y=\"3\" width=\"7\" height=\"7\" rx=\"1\"/><rect x=\"14\" y=\"3\" width=\"7\" height=\"7\" rx=\"1\"/><rect x=\"3\" y=\"14\" width=\"7\" height=\"7\" rx=\"1\"/><rect x=\"14\" y=\"14\" width=\"7\" height=\"7\" rx=\"1\"/>",
  wallet: "<rect x=\"3\" y=\"5\" width=\"18\" height=\"15\" rx=\"3\"/><path d=\"M3 9h18\"/><path d=\"M16 14h2\"/>",
  people: "<circle cx=\"9\" cy=\"8\" r=\"3\"/><path d=\"M3 20v-2a6 6 0 0 1 12 0v2\"/><path d=\"M17 5a3 3 0 0 1 0 6M17 14a5 5 0 0 1 4 5\"/>",
  calendar: "<rect x=\"3\" y=\"5\" width=\"18\" height=\"16\" rx=\"2\"/><path d=\"M7 3v4M17 3v4M3 10h18\"/>",
  chart: "<path d=\"M3 3v18h18\"/><path d=\"m7 15 4-4 3 3 6-7\"/>",
  link: "<path d=\"M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7\"/><path d=\"M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7\"/>",
  plus: "<path d=\"M12 5v14M5 12h14\"/>",
  search: "<circle cx=\"11\" cy=\"11\" r=\"7\"/><path d=\"m20 20-4-4\"/>",
  bell: "<path d=\"M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4\"/>",
  moon: "<path d=\"M20 15.5A8 8 0 0 1 8.5 4 8.5 8.5 0 1 0 20 15.5Z\"/>",
  sun: "<circle cx=\"12\" cy=\"12\" r=\"4\"/><path d=\"M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4\"/>",
  arrow: "<path d=\"M5 12h14m-6-6 6 6-6 6\"/>",
  up: "<path d=\"m7 17 10-10M8 7h9v9\"/>",
  down: "<path d=\"m7 7 10 10M8 17h9V8\"/>",
  clock: "<circle cx=\"12\" cy=\"12\" r=\"9\"/><path d=\"M12 7v5l3 2\"/>",
  file: "<path d=\"M6 3h8l5 5v13H6zM14 3v6h5M9 14h7M9 18h7\"/>",
  download: "<path d=\"M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4\"/>",
  menu: "<path d=\"M4 7h16M4 12h16M4 17h16\"/>",
  check: "<path d=\"m4 12 5 5L20 6\"/>",
  back: "<path d=\"m15 18-6-6 6-6\"/>",
  shield: "<path d=\"M12 2 4 6v6c0 5 3.5 8 8 10 4.5-2 8-5 8-10V6zM9 12l2 2 4-4\"/>",
  filter: "<path d=\"M4 6h16M7 12h10m-7 6h4\"/>",
  logout: "<path d=\"M10 3H4v18h6M14 7l5 5-5 5m-8-5h13\"/>",
  chat: "<path d=\"M4 20l1.5-4A8 8 0 1 1 9 19.5z\"/><path d=\"M9 10h.01M12 10h.01M15 10h.01\"/>",
  phone: "<path d=\"M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2\"/>",
  spark: "<path d=\"m12 2 2.4 7.6L22 12l-7.6 2.4L12 22l-2.4-7.6L2 12l7.6-2.4z\"/>",
  receipt: "<path d=\"M6 3h12v18l-3-2-3 2-3-2-3 2z\"/><path d=\"M9 8h6M9 12h6\"/>",
  upload: "<path d=\"M12 16V4m0 0-4 4m4-4 4 4\"/><path d=\"M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3\"/>",
  home: "<path d=\"M3 11 12 4l9 7\"/><path d=\"M5 10v10h14V10\"/>",
  alert: "<path d=\"M12 3 2 20h20z\"/><path d=\"M12 10v4M12 17h.01\"/>",
  x: "<path d=\"M6 6l12 12M18 6 6 18\"/>",
  trash: "<path d=\"M4 7h16M10 11v6M14 11v6\"/><path d=\"M6 7l1 13h10l1-13M9 7V4h6v3\"/>",
  copy: "<rect x=\"9\" y=\"9\" width=\"11\" height=\"11\" rx=\"2\"/><path d=\"M5 15V5a2 2 0 0 1 2-2h10\"/>",
  eye: "<path d=\"M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z\"/><circle cx=\"12\" cy=\"12\" r=\"3\"/>",
  edit: "<path d=\"M4 20h4L19 9l-4-4L4 16z\"/><path d=\"m14 6 4 4\"/>",
  bank: "<path d=\"M3 10 12 4l9 6\"/><path d=\"M5 10v8M9 10v8M15 10v8M19 10v8M3 20h18\"/>",
  activity: "<path d=\"M3 12h4l3-8 4 16 3-8h4\"/>",
  inbox: "<path d=\"M4 13h4l2 3h4l2-3h4\"/><path d=\"M4 13 6 5h12l2 8v6H4z\"/>",
  outbox: "<path d=\"M12 3v10m0-10-4 4m4-4 4 4\"/><path d=\"M4 14v5a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5\"/>",
  print: "<path d=\"M7 9V3h10v6\"/><rect x=\"3\" y=\"9\" width=\"18\" height=\"8\" rx=\"2\"/><path d=\"M7 14h10v7H7z\"/>",
  percent: "<path d=\"M19 5 5 19\"/><circle cx=\"7\" cy=\"7\" r=\"2.5\"/><circle cx=\"17\" cy=\"17\" r=\"2.5\"/>",
  diamond: "<path d=\"M12 3 21 12l-9 9-9-9z\"/>",
  user: "<circle cx=\"12\" cy=\"8\" r=\"4\"/><path d=\"M4 21a8 8 0 0 1 16 0\"/>",
};

export type IconName = keyof typeof ICONS;

export function Icon({ name, className = "size-[18px]" }: { name: string; className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.9}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
      dangerouslySetInnerHTML={{ __html: ICONS[name] ?? ICONS.dot ?? "" }} />
  );
}
