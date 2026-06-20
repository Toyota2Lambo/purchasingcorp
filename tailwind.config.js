/** Shared config for all static pages, replaces the per-page inline
 * `tailwind.config` that the CDN script used to read. */
module.exports = {
  content: ['./*.html', './pricing.js', './pricing-data.js'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
      colors: {
        ink: {
          950: '#050505',
          900: '#0a0a0a',
          850: '#0f0f10',
          800: '#141416',
          700: '#1c1c1f',
          600: '#26262b',
          500: '#3a3a40',
          400: '#5a5a62',
          300: '#8a8a92',
          200: '#b6b6bc',
          100: '#e6e6e8',
        },
      },
      boxShadow: {
        glow: '0 0 0 1px rgba(255,255,255,0.06), 0 10px 40px -10px rgba(0,0,0,0.6)',
        inset: 'inset 0 1px 0 0 rgba(255,255,255,0.06)',
      },
    },
  },
};
