/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./*.html", // Only looks at HTML files in this main folder
    "./*.js"    // Only looks at JS files in this main folder
  ],
  theme: {
    extend: {
      fontFamily: { sans: ['Inter', 'sans-serif'] },
      colors: { brandDark: '#0f172a', brandAmber: '#f59e0b' }
    }
  },
  darkMode: 'class', 
  plugins: [],
}