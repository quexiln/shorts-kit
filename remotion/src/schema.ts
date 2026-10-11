// props.json contract between the Python pipeline and this composition.
// Keep identical on both sides; change them together.

export type Sec = number;
export type Theme = {accent: string; text: string; dark: string; font: string; channelName: string; handle: string;
                     illustrativeLabel: string};
export type VideoProps = {
  durationSec: Sec;
  theme: Theme;
  voice: {src: string; volume?: number};
  music?: {src: string; volume?: number};
  sfx?: {src: string; at: Sec; volume?: number}[];
  words: {text: string; start: Sec; end: Sec; key?: boolean}[];
  captionsOff?: {from: Sec; to: Sec}[];
  hook: {title: string; until: Sec};                       // *emphasis* and \n supported
  shots: {src: string; kind: 'video' | 'image'; at: Sec; dur: Sec; from?: Sec; focusX?: number; focusY?: number;
          zoom?: 'none' | 'punch' | 'slow'; illustrative?: boolean; credit?: string}[];
  overlays: (
    | {type: 'source'; at: Sec; dur: Sec; source: string; date?: string}
    | {type: 'stat'; at: Sec; dur: Sec; title: string; unit?: string; items: {label: string; value: number; highlight?: boolean}[]}
    | {type: 'quote'; at: Sec; dur: Sec; quote: string; name: string; role?: string; highlight?: string}
  )[];
  outro: {at: Sec; text: string};                          // closing line + theme.handle
};

// Derived helper types (not part of the contract).
export type Word = VideoProps['words'][number];
export type Shot = VideoProps['shots'][number];
export type Overlay = VideoProps['overlays'][number];
export type OverlayOf<T extends Overlay['type']> = Extract<Overlay, {type: T}>;
