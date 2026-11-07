"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { api } from "@/lib/api";

const LINKS = [
  { href: "/", label: "Panorama" },
  { href: "/eeuu", label: "EE.UU." },
  { href: "/liquidez", label: "Liquidez" },
  { href: "/argentina", label: "Argentina" },
  { href: "/macro", label: "Macro global" },
  { href: "/mercados", label: "Mercados" },
  { href: "/tecnico", label: "Técnico" },
  { href: "/empresas", label: "Empresas" },
  { href: "/screener", label: "Screener" },
  { href: "/sistema", label: "Sistema" },
];

export function TopBar() {
  const pathname = usePathname();
  const router = useRouter();

  async function logout() {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } finally {
      router.replace("/login");
      router.refresh();
    }
  }

  return (
    <header className="topbar">
      <div className="topbar-inner">
        <Link href="/" className="brand">
          Market <span>Analyst</span>
        </Link>
        <nav className="nav" aria-label="Secciones">
          {LINKS.map((l) => (
            <Link key={l.href} href={l.href} aria-current={pathname === l.href ? "page" : undefined}>
              {l.label}
            </Link>
          ))}
        </nav>
        <button className="link" onClick={logout}>
          Salir
        </button>
      </div>
    </header>
  );
}
