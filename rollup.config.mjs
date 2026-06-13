import deckyPlugin from "@decky/rollup";

const config = deckyPlugin();
config.output = {
  ...config.output,
  format: "iife",
  globals: {
    react: "SP_REACT",
    "react-dom": "SP_REACTDOM",
    "@decky/ui": "DFL",
  },
};

export default config;
