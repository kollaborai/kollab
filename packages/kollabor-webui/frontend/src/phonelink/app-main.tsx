// The phone app. It is served by the user's own computer and runs only after
// the loader has checked the computer's signature on these exact bytes.
import { createRoot } from "react-dom/client";
import { TooltipProvider } from "@/components/ui/tooltip";
import App from "@/App";
import "@/styles.css";

document.documentElement.classList.add("dark");
createRoot(document.getElementById("root")!).render(
  <TooltipProvider>
    <div className="h-dvh min-h-0 overflow-hidden">
      <App contained />
    </div>
  </TooltipProvider>,
);
