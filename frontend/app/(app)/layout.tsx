import { LiveStrip } from "@/components/LiveStrip";
import { TopBar } from "@/components/TopBar";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="shell">
      <div className="sticky-head">
        <TopBar />
        <LiveStrip />
      </div>
      <main className="main">{children}</main>
    </div>
  );
}
