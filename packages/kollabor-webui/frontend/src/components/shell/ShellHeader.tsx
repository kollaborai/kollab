import { useLayoutEffect, useRef, type ComponentPropsWithoutRef } from "react";
import { useSidebar } from "@/components/ui/sidebar";

/** Keep the sidebar border aligned with the currently rendered chat header. */
export function ShellHeader(props: ComponentPropsWithoutRef<"header">) {
  const headerRef = useRef<HTMLElement>(null);
  const { setShellHeaderHeight } = useSidebar();

  useLayoutEffect(() => {
    const header = headerRef.current;
    if (!header) return;
    let active = true;
    const measure = () => {
      if (!active) return;
      const height = header.getBoundingClientRect().height;
      if (height > 0) setShellHeaderHeight(height);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(header, { box: "border-box" });
    return () => {
      active = false;
      observer.disconnect();
      setShellHeaderHeight(null);
    };
  }, [setShellHeaderHeight]);

  return <header {...props} ref={headerRef} data-slot="shell-header" />;
}
