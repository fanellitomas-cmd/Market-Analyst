import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Market Analyst",
  description: "Macro por país y análisis de activos contra el S&P 500.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
