/**
 * Layer contract for the Qupi mascot art. Every layer is a WebP with alpha on the same
 * square canvas (240x240 as `<layer>@2x.webp`, 120x120 as `<layer>.webp`); stack them at 0,0.
 * Head, ears, mark, eyelids and both mouth overlays share the head transform (rotate about
 * `head`); an ear then adds its own rotation about its root.
 */
export const QUPI_LAYERS = [
  'tail',
  'body',
  'ear-left',
  'ear-right',
  'head',
  'mark',
  'eyelids',
  'mouth-closed',
  'mouth-half',
] as const

export type QupiLayer = (typeof QUPI_LAYERS)[number]

/** transform-origin per layer, as CSS percentages of the canvas. */
export const QUPI_ORIGINS: Record<QupiLayer, string> = {
  tail: '38.7% 86.8%',
  body: '59.4% 69.8%',
  'ear-left': '54.7% 34.3%',
  'ear-right': '85.5% 38.1%',
  head: '64.2% 67.9%',
  mark: '71.7% 37.4%',
  eyelids: '64.2% 67.9%',
  'mouth-closed': '64.2% 67.9%',
  'mouth-half': '64.2% 67.9%',
}
