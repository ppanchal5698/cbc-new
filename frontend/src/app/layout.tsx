import type { Metadata } from "next";
import { Manrope } from "next/font/google";
import "@phosphor-icons/web/duotone";
import "./globals.css";
import { Providers } from "./providers";

// The prototype's --app-font. Loaded through next/font so it is self-hosted:
// customer drawings and pricing sit behind this app, and a font request to a
// third party on every page load is a needless outbound call (NFR-4).
const manrope = Manrope({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-manrope",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Ops-Hub · Hamilton Parker",
  description: "The estimating and pricing desk for CBC.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={manrope.variable}>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
