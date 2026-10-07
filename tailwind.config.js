/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./dashboard/templates/**/*.html",
    "./dashboard/static/js/**/*.js",
  ],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: ["-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["SFMono-Regular", "Cascadia Code", "Liberation Mono", "Consolas", "monospace"],
      },
    },
  },
};
