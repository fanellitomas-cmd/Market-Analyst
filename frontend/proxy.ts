import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Redirección rápida a /login si no hay cookie de sesión. Es sólo comodidad de navegación:
// la validación real del token la hace el backend en cada pedido a /api.
export function proxy(request: NextRequest) {
  const hasSession = request.cookies.has("ma_session");
  const { pathname, search } = request.nextUrl;

  if (!hasSession && pathname !== "/login") {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = `?next=${encodeURIComponent(pathname + search)}`;
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
