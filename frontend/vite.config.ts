import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";

export const backendPathPattern =
  "^/(agent|ui|operator|customers|orders|tickets|memories|escalations)(?:/|$)|^/conversations/|^/(health|ready)$";

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, "..", "");
  const backendTarget = environment.VITE_BACKEND_TARGET || "http://localhost:8000";
  const runtimeEnvironment = (
    globalThis as typeof globalThis & {
      process?: { env: Record<string, string | undefined> };
    }
  ).process?.env;
  const configuredAuthMode =
    runtimeEnvironment?.FRONTEND_AUTH_MODE ||
    environment.FRONTEND_AUTH_MODE ||
    (mode === "production" ? "external_session" : "local_demo");
  const authMode = ["local_demo", "integration", "external_session"].includes(
    configuredAuthMode,
  )
    ? configuredAuthMode
    : "external_session";
  const demoToken =
    authMode === "local_demo" || authMode === "integration"
      ? runtimeEnvironment?.LOCAL_DEMO_AUTH_TOKEN || environment.LOCAL_DEMO_AUTH_TOKEN || ""
      : "";

  return {
    envDir: "..",
    define: {
      "import.meta.env.VITE_AUTH_MODE": JSON.stringify(authMode),
      "import.meta.env.VITE_DEMO_AUTH_TOKEN": JSON.stringify(demoToken),
    },
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
    },
    server: {
      port: 5173,
      proxy: {
        [backendPathPattern]: {
          target: backendTarget,
          changeOrigin: true,
        },
      },
    },
  };
});
