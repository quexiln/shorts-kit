import {loadFont as loadInter} from '@remotion/google-fonts/Inter';
import {loadFont as loadMontserrat} from '@remotion/google-fonts/Montserrat';
import {loadFont as loadPoppins} from '@remotion/google-fonts/Poppins';
import {loadFont as loadRoboto} from '@remotion/google-fonts/Roboto';

// Safe area: platform UI (caption, buttons, progress bar) covers the edges of a 1080x1920 frame.
export const SAFE = {top: 220, bottom: 420, left: 60, right: 150} as const;
export const SAFE_X0 = SAFE.left;
export const SAFE_X1 = 1080 - SAFE.right;
export const SAFE_W = SAFE_X1 - SAFE_X0;
export const SAFE_Y1 = 1920 - SAFE.bottom;

/** Centre line of the caption block. */
export const CAPTION_Y = 1240;

const WEIGHTS = ['400', '600', '700', '800'];
const SUBSETS = ['latin', 'latin-ext'];

// Only a short whitelist so the bundle stays small; anything else falls back to Inter.
const LOADERS: Record<string, () => {fontFamily: string}> = {
  Inter: () => loadInter('normal', {weights: WEIGHTS as never, subsets: SUBSETS as never}),
  Montserrat: () => loadMontserrat('normal', {weights: WEIGHTS as never, subsets: SUBSETS as never}),
  Poppins: () => loadPoppins('normal', {weights: WEIGHTS as never, subsets: SUBSETS as never}),
  Roboto: () => loadRoboto('normal', {weights: ['400', '500', '700', '900'] as never, subsets: SUBSETS as never}),
};

const loaded = new Map<string, string>();

/** Loads the theme font once and returns a CSS font-family stack. */
export const fontFamily = (name: string): string => {
  const key = LOADERS[name] ? name : 'Inter';
  if (!LOADERS[name]) console.warn(`[theme] font "${name}" not supported, using Inter`);
  let family = loaded.get(key);
  if (!family) {
    family = LOADERS[key]().fontFamily;
    loaded.set(key, family);
  }
  return `"${family}", system-ui, sans-serif`;
};

/** Hex colour with alpha (0..1). Accepts #rgb and #rrggbb; other values are returned unchanged. */
export const alpha = (hex: string, a: number): string => {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return hex;
  const h = m[1].length === 3 ? [...m[1]].map((c) => c + c).join('') : m[1];
  const n = parseInt(h, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
};

/** Splits "*a* b" into runs; `*` toggles emphasis, `\n` is a forced line break. */
export type Run = {text: string; em: boolean; br?: boolean};
export const parseEmphasis = (s: string): Run[] => {
  const out: Run[] = [];
  let em = false;
  for (const part of s.split(/(\*|\n)/)) {
    if (part === '*') em = !em;
    else if (part === '\n') out.push({text: '', em, br: true});
    else if (part) out.push({text: part, em});
  }
  return out;
};
