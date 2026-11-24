/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'Segoe UI', 'sans-serif'],
      },
      colors: {
        brand: {
          50: '#eef5f2',
          100: '#d7e8df',
          200: '#b0d1bf',
          300: '#82b49a',
          400: '#599478',
          500: '#3c7a5d',
          600: '#2d6149',
          700: '#254d3b',
          800: '#1e3d31',
          900: '#182f27',
        },
      },
      boxShadow: {
        card: '0 1px 2px rgba(16,24,40,.04), 0 8px 24px rgba(16,24,40,.04)',
      },
    },
  },
  plugins: [],
}
