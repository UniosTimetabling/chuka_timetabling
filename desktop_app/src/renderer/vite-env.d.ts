/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Server address the login screen starts with (set by run.sh for local testing). */
  readonly VITE_DEFAULT_SERVER_URL?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
