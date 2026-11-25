import { fileURLToPath } from "node:url";

export default {
  build: {
    lib: {
      entry: fileURLToPath(new URL("./src/index.js", import.meta.url)),
      formats: ["es"],
      fileName: () => "index.js",
      cssFileName: "index",
    },
    rollupOptions: { external: ["react", "react-dom", "react/jsx-runtime", "@kollab/plugin"] },
  },
};
