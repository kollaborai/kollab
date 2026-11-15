import { describe, expect, it } from "vitest";
import { pluginUI } from "./ui";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip } from "@/components/ui/tooltip";
import { HoverHelp } from "@/components/ui/hover-help";
import { ScrollArea } from "@/components/ui/scroll-area";
import { FieldRenderer } from "@/components/panels/FieldRenderer";
import { GemAvatar } from "@/components/gems/GemAvatar";

describe("plugin UI primitives", () => {
  it("exposes the existing app primitives promised by the plugin contract", () => {
    expect(pluginUI).toMatchObject({ Sheet, SheetContent, Switch, Skeleton, Tooltip, HoverHelp, ScrollArea, FieldRenderer, GemAvatar });
  });
});
