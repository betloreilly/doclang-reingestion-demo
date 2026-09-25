import type { Config } from "tailwindcss";

export default {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "var(--background)",
        foreground: "var(--foreground)",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "Source Sans 3", "ui-sans-serif", "system-ui"],
        display: ["var(--font-display)", "Fraunces", "ui-serif", "Georgia"],
      },
    },
  },
  plugins: [],
} satisfies Config;
