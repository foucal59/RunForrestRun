/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: '#6366F1',
        primary: { DEFAULT: '#6366F1', light: '#818CF8', dark: '#4F46E5' },
        secondary: { DEFAULT: '#0EA5E9', light: '#38bdf8' },
        surface: {
          DEFAULT: '#F8FAFC',
          card: '#FFFFFF',
          hover: '#F1F5F9',
          border: '#E2E8F0',
          muted: '#F1F5F9',
        },
        txt: {
          DEFAULT: '#1E293B',
          secondary: '#64748B',
          muted: '#94A3B8',
        },
        accent: {
          DEFAULT: '#6366F1',
          light: '#818CF8',
          dim: '#4F46E5',
        },
      },
      fontFamily: {
        sans: ['Outfit', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      boxShadow: {
        card: '0 1px 3px 0 rgb(0 0 0 / 0.06), 0 1px 2px -1px rgb(0 0 0 / 0.06)',
        'card-hover': '0 4px 6px -1px rgb(0 0 0 / 0.07), 0 2px 4px -2px rgb(0 0 0 / 0.07)',
      },
    },
  },
  plugins: [],
}
