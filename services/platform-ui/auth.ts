import NextAuth from "next-auth"
import Keycloak from "next-auth/providers/keycloak"

const keycloakUrl = process.env.KEYCLOAK_URL ?? "http://localhost:8083"
const keycloakRealm = process.env.KEYCLOAK_REALM ?? "inference-platform"

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [
    Keycloak({
      clientId: "platform-ui",
      clientSecret: process.env.PLATFORM_UI_CLIENT_SECRET!,
      issuer: `${keycloakUrl}/realms/${keycloakRealm}`,
    }),
  ],
  callbacks: {
    async jwt({ token, account, profile }) {
      if (account) {
        token.access_token = account.access_token
        // Keycloak realm-roles mapper emits roles[] and team claims
        const p = profile as Record<string, unknown> | undefined
        token.roles = (p?.roles as string[]) ?? []
        token.team = (p?.team as string) ?? ""
      }
      return token
    },
    async session({ session, token }) {
      return {
        ...session,
        user: {
          ...session.user,
          roles: (token.roles as string[]) ?? [],
          team: (token.team as string) ?? "",
          access_token: token.access_token as string | undefined,
        },
      }
    },
  },
})
