import { createRoot } from "react-dom/client";
import { TooltipProvider } from "@/components/ui/tooltip";
import App from "./App";
import "./styles.css";

// assistant-ui's kit is authored for a dark-capable shadcn theme; kollab's
// terminal identity is dark, so pin the class here rather than shipping a
// half-wired theme toggle.
document.documentElement.classList.add("dark");

// No <StrictMode>: its dev-only double render breaks the assistant-ui store
// (@assistant-ui/store 0.3.2 on tap 0.9.8). Each pass builds its own
// notification manager; React keeps the client from the first pass while the
// committed effect notifies the second, so no useAuiState hook re-renders and
// the composer drops typed text. Production renders once either way.
// @assistant-ui/react 0.15.25 (store 0.3.17, tap 0.9.21) types fine under
// StrictMode; put it back with that upgrade.
createRoot(document.getElementById("root")!).render(
  <TooltipProvider>
    <App />
  </TooltipProvider>,
);
