/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#060812",
        surface: "#0c1022",
        panel: "#101734",
        neon: "#63f3ff",
        violet: "#9f7aea",
        glow: "#bc7cff",
      },
      fontFamily: {
        display: ["Space Grotesk", "sans-serif"],
        body: ["Inter", "sans-serif"],
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(188,124,255,0.24), 0 0 30px rgba(91,33,182,0.28)",
      },
    },
  },
  plugins: [],
};
