import { auth } from "./auth"
import { NextResponse } from "next/server"
import type { NextRequest } from "next/server"

export default auth((req: NextRequest & { auth: unknown }) => {
  const { pathname } = req.nextUrl

  // next-auth callbacks and health endpoint are always public
  if (pathname.startsWith("/api/auth") || pathname === "/health") {
    return NextResponse.next()
  }

  // Unauthenticated requests are redirected to Keycloak via next-auth sign-in
  if (!req.auth) {
    return NextResponse.redirect(new URL("/api/auth/signin", req.url))
  }

  return NextResponse.next()
})

export const config = {
  // Exclude Next.js internals and static assets from middleware
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
}
