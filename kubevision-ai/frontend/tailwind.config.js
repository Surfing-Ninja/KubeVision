/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        display: ["Space Grotesk", "ui-sans-serif", "system-ui"],
        body: ["Instrument Sans", "ui-sans-serif", "system-ui"],
        mono: ["IBM Plex Mono", "ui-monospace", "SFMono-Regular"],
      },
      boxShadow: {
        panel: "0 14px 32px rgba(15, 23, 42, 0.12)",
        float: "0 26px 60px rgba(15, 23, 42, 0.18)",
      },
    },
  },
  plugins: [],
};
