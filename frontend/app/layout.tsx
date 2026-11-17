import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Audio Quality Gatekeeper",
  description: "Diagnose, repair and master audio",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
