import type { ComponentProps } from "react";
import { NoticeBar } from "@/components/shell/NoticeBar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Sheet, SheetTrigger, SheetClose, SheetContent, SheetHeader, SheetFooter, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip";
import { HoverHelp } from "@/components/ui/hover-help";
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area";
import { FieldRenderer } from "@/components/panels/FieldRenderer";
import { GemAvatar } from "@/components/gems/GemAvatar";

// The shell has no standalone table primitive: use semantic HTML and its existing typography.
function Table(props: ComponentProps<"table">) {
  return <div className="w-full overflow-x-auto"><table {...props} /></div>;
}

export const pluginUI = {
  Alert: NoticeBar, Badge, Button, Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle,
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, Input, Label,
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue, Table, Tabs, TabsContent, TabsList, TabsTrigger,
  Sheet, SheetTrigger, SheetClose, SheetContent, SheetHeader, SheetFooter, SheetTitle, SheetDescription,
  Switch, Skeleton, Tooltip, TooltipTrigger, TooltipContent, TooltipProvider, HoverHelp,
  ScrollArea, ScrollBar, FieldRenderer, GemAvatar,
};
export type PluginUi = typeof pluginUI;
