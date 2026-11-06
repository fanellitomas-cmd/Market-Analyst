import { TopBar } from "@/components/TopBar";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="shell">
      <TopBar />
      <main className="main">{children}</main>
    </div>
  );
}
