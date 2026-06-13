import type { DefaultSession, DefaultJWT } from "next-auth"

declare module "next-auth" {
  interface Session {
    user: DefaultSession["user"] & {
      roles: string[]
      team: string
      access_token?: string
    }
  }
}

declare module "next-auth/jwt" {
  interface JWT extends DefaultJWT {
    roles: string[]
    team: string
    access_token?: string
  }
}
