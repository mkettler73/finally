import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import "./globals.css";

/**
 * Two weights of one family. Plex was drawn for engineered, institutional
 * contexts and has the tabular figures this interface leans on constantly;
 * the mono cut is reserved for tickers and prices, where character-cell
 * alignment is information rather than texture.
 */
const plexSans = IBM_Plex_Sans({
  variable: "--font-plex-sans",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  variable: "--font-plex-mono",
  subsets: ["latin"],
  weight: ["400", "500"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "FinAlly",
  description: "A live trading terminal with an AI copilot.",
};

export const viewport: Viewport = {
  themeColor: "#0d1117",
  colorScheme: "dark",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${plexSans.variable} ${plexMono.variable} h-full`}>
      <body className="h-full">{children}</body>
    </html>
  );
}
