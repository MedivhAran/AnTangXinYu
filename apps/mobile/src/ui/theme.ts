/**
 * Cool, quiet colors shared by the mobile app.
 *
 * The names describe UI roles instead of individual screens so the same
 * colors can be reused without gradually introducing near-duplicates.
 */
export const colors = {
  background: '#F5F9F8',
  paper: '#FFFFFF',
  paperMuted: '#EDF5F3',
  ink: '#17302E',
  text: '#29413E',
  muted: '#667875',
  faint: '#91A19E',
  line: '#E4ECEA',
  primary: '#20B894',
  primaryPressed: '#11866F',
  primarySoft: '#E7F7F2',
  coral: '#20B894',
  coralPressed: '#11866F',
  coralSoft: '#E7F7F2',
  danger: '#DE665B',
  dangerSoft: '#FCEDEA',
  disabled: '#C1CECB',
  white: '#FFFFFF',
} as const;

export const spacing = {
  xxs: 4,
  xs: 8,
  sm: 12,
  md: 16,
  lg: 20,
  xl: 24,
  xxl: 32,
  xxxl: 40,
} as const;

export const radii = {
  sm: 10,
  md: 16,
  lg: 22,
  xl: 28,
  pill: 999,
} as const;

export const typefaces = {
  serif: 'sans-serif',
  sans: 'sans-serif',
  sansMedium: 'sans-serif-medium',
} as const;
