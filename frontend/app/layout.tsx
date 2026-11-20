import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";

import "./globals.css";

// Self-hosted (SIL Open Font License, see app/fonts/OFL-*.txt). Nothing is fetched at build time.
const ui = localFont({
  src: "./fonts/AtkinsonHyperlegibleNext-Variable.ttf",
  variable: "--font-ui",
  weight: "200 800",
  display: "swap",
});
const readout = localFont({
  src: "./fonts/MartianMono-Variable.ttf",
  variable: "--font-readout",
  weight: "100 800",
  display: "swap",
  declarations: [{ prop: "font-stretch", value: "75% 112.5%" }],
});

export const metadata: Metadata = {
  title: "Audio Gatekeeper",
  description: "Checks uploaded audio for damage, repairs what it can, and masters it for streaming.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0e1622" },
    { media: "(prefers-color-scheme: light)", color: "#eef1f5" },
  ],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${ui.variable} ${readout.variable}`}>
      <body>{children}</body>
    </html>
  );
}
