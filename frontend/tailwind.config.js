/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        page: 'var(--color-page)',
        background: 'var(--color-background)',
        foreground: 'var(--color-foreground)',
        card: 'var(--color-card)',
        border: 'var(--color-border)',
        muted: 'var(--color-muted)',
        accent: '#3b82f6',
        'accent-dark': '#2563eb',
        destructive: '#dc2626',
        warning: '#d97706',
        sidebar: 'var(--color-sidebar)',
        'sidebar-border': 'var(--color-sidebar-border)',
        'sidebar-active': 'var(--color-sidebar-active)',
      },
      animation: {
        'gradient-x': 'gradient-x 1.5s ease infinite',
      },
      keyframes: {
        'gradient-x': {
          '0%, 100%': { 'background-position': '0% 50%' },
          '50%': { 'background-position': '100% 50%' },
        },
      },
    },
  },
  plugins: [require('tailwindcss-animate')],
}
