import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { TooltipProvider } from "@/components/ui/tooltip";
import App from "./App";
import "./styles.css";

// assistant-ui's kit is authored for a dark-capable shadcn theme; kollab's
// terminal identity is dark, so pin the class here rather than shipping a
// half-wired theme toggle.
document.documentElement.classList.add("dark");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <TooltipProvider>
      <App />
    </TooltipProvider>
  </StrictMode>,
);
