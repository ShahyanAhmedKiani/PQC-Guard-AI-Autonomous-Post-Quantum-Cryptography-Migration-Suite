/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          950: '#07080B',
          900: '#0B0D12',
          800: '#12151C',
          700: '#1A1E27',
          600: '#242938',
        },
        parchment: {
          50: '#FBF7ED',
          100: '#F5EEDA',
          200: '#EBE0C4',
        },
        gilt: {
          300: '#E9D9A8',
          400: '#D9BE73',
          500: '#C9A84C',
          600: '#A8863A',
          700: '#8A6C2C',
        },
      },
      fontFamily: {
        serif: ['"Source Serif 4"', '"Iowan Old Style"', 'Georgia', 'serif'],
        mono: ['"IBM Plex Mono"', '"JetBrains Mono"', 'monospace'],
        sans: ['"Inter"', 'system-ui', 'sans-serif'],
      },
      boxShadow: {
        glass: '0 8px 32px 0 rgba(0, 0, 0, 0.45)',
        'gilt-glow': '0 0 0 1px rgba(201, 168, 76, 0.35), 0 4px 18px rgba(201, 168, 76, 0.15)',
      },
      backdropBlur: {
        xs: '2px',
      },
    },
  },
  plugins: [],
};
