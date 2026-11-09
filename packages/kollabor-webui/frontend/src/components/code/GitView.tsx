import { GitPanel } from "@/components/code/git/GitPanel";
import { openWorkspaceDiff, openWorkspaceFile } from "@/components/code/open-file";
import { claimEditorWorkspace } from "@/components/code/workspace-tabs";

export interface GitViewProps {
  workspace: string;
  /** Called after a file or diff is opened, so the session shows the Code Editor where it now is. */
  onShowEditor: () => void;
}

/** The Git view for one chat folder, docked in the chat column: status, stage, commit, branches, stash, log. */
export function GitView({ workspace, onShowEditor }: GitViewProps) {
  const folderLabel = workspace.split("/").filter(Boolean).slice(-2).join("/");

  const openInEditor = async (open: () => Promise<unknown>) => {
    // tie the editor's tabs to this folder first, so its own mount does not drop what is opened here
    claimEditorWorkspace(workspace);
    await open();
    onShowEditor();
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden" data-git-view="">
      <div className="flex shrink-0 items-center gap-1.5 px-3 py-1.5 text-xs font-medium">
        <span className="min-w-0 truncate">
          Git <span className="font-mono text-[10px] font-normal text-muted-foreground">{folderLabel}</span>
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        <div className="mx-auto w-full max-w-3xl overflow-hidden rounded-lg bg-sidebar text-sidebar-foreground">
          <GitPanel
            workspace={workspace}
            onOpenFile={(absPath) => void openInEditor(() => openWorkspaceFile(workspace, absPath, { pin: true }))}
            onOpenDiff={(absPath, headContent) =>
              void openInEditor(() => openWorkspaceDiff(workspace, absPath, headContent))
            }
          />
        </div>
      </div>
    </div>
  );
}
