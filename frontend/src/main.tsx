import React from "react";
import ReactDOM from "react-dom/client";
import "./styles.css";
import { applyTheme, readThemePreference } from "./lib/theme";
import { App } from "./app/App";

applyTheme(readThemePreference());

ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
