/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: ["class"],
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive) / <alpha-value>)",
          foreground: "hsl(var(--destructive-foreground) / <alpha-value>)",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        sidebar: {
          DEFAULT: "hsl(var(--sidebar-background))",
          foreground: "hsl(var(--sidebar-foreground))",
          primary: "hsl(var(--sidebar-primary))",
          "primary-foreground": "hsl(var(--sidebar-primary-foreground))",
          accent: "hsl(var(--sidebar-accent))",
          "accent-foreground": "hsl(var(--sidebar-accent-foreground))",
          border: "hsl(var(--sidebar-border))",
          ring: "hsl(var(--sidebar-ring))",
        },
        highlight: "#ea0000",
        "dark-gray": "#1a1a1a",
        "light-gray": "#f1f1f1",
        "alpha-white": "rgba(255, 255, 255, 0.5)",
        "deep-black": "#0a0a0a",
        "chrome-white": "#f8f8f8",
      },
      borderRadius: {
        xl: "calc(var(--radius) + 4px)",
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
        xs: "calc(var(--radius) - 6px)",
      },
      boxShadow: {
        xs: "0 1px 2px 0 rgb(0 0 0 / 0.05)",
        glow: "0 0 40px rgba(234, 0, 0, 0.2)",
        "glow-strong": "0 0 60px rgba(234, 0, 0, 0.4)",
      },
      fontFamily: {
        sans: ['Geist', 'sans-serif'],
      },
      fontSize: {
        'display-xxl': ['168px', { lineHeight: '1.15', fontWeight: '500' }],
        'display-xl': ['126px', { lineHeight: '1', fontWeight: '500' }],
        'h1': ['71.66px', { lineHeight: '1.15', fontWeight: '400' }],
        'h2': ['59.72px', { lineHeight: '1.3', fontWeight: '400' }],
        'h3': ['49.77px', { lineHeight: '1.15', fontWeight: '400' }],
        'h4': ['41.47px', { lineHeight: '1.15', fontWeight: '400' }],
        'h5': ['34.56px', { lineHeight: '1.2', fontWeight: '400' }],
        'h6': ['28.8px', { lineHeight: '1.2', fontWeight: '400' }],
        'body-lg': ['20px', { lineHeight: '1.5' }],
        'body': ['16px', { lineHeight: '1.5' }],
        'body-sm': ['14px', { lineHeight: '1.43' }],
      },
      keyframes: {
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
        "caret-blink": {
          "0%,70%,100%": { opacity: "1" },
          "20%,50%": { opacity: "0" },
        },
        "marquee": {
          "0%": { transform: "translateX(0)" },
          "100%": { transform: "translateX(-50%)" },
        },
        "pulse-glow": {
          "0%, 100%": { opacity: "0.3" },
          "50%": { opacity: "1" },
        },
        "float": {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-10px)" },
        },
        "line-grow": {
          "0%": { height: "0" },
          "100%": { height: "200px" },
        },
        "blur-reveal": {
          "0%": { filter: "blur(20px)", opacity: "0" },
          "100%": { filter: "blur(0)", opacity: "1" },
        },
        "slide-up": {
          "0%": { transform: "translateY(40px)", opacity: "0" },
          "100%": { transform: "translateY(0)", opacity: "1" },
        },
        "scale-in": {
          "0%": { transform: "scale(0.9)", opacity: "0" },
          "100%": { transform: "scale(1)", opacity: "1" },
        },
        "flip-in": {
          "0%": { transform: "rotateY(-90deg)", opacity: "0" },
          "100%": { transform: "rotateY(0deg)", opacity: "1" },
        },
        "width-expand": {
          "0%": { width: "0" },
          "100%": { width: "100%" },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
        "caret-blink": "caret-blink 1.25s ease-out infinite",
        "marquee": "marquee 25s linear infinite",
        "pulse-glow": "pulse-glow 3s ease-in-out infinite",
        "float": "float 4s ease-in-out infinite",
        "line-grow": "line-grow 1.5s cubic-bezier(0.87, 0, 0.13, 1) forwards",
        "blur-reveal": "blur-reveal 0.8s cubic-bezier(0.4, 0, 0.2, 1) forwards",
        "slide-up": "slide-up 0.7s cubic-bezier(0.16, 1, 0.3, 1) forwards",
        "scale-in": "scale-in 0.7s cubic-bezier(0.16, 1, 0.3, 1) forwards",
        "flip-in": "flip-in 0.9s cubic-bezier(0.34, 1.56, 0.64, 1) forwards",
        "width-expand": "width-expand 1s cubic-bezier(0.87, 0, 0.13, 1) forwards",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
}
