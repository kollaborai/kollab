import { useLayoutEffect, useState } from "react";
import { createRoot } from "react-dom/client";

import type { EngineApi, MediaEntry } from "@/api";
import {
  MediaImageProvider,
  MediaSessionProvider,
  MediaThumb,
  MediaViewerProvider,
  useMediaViewer,
  useSessionMedia,
} from "@/components/media/media-view";
import "@/styles.css";

const SESSION_A_IMAGE: MediaEntry = { ref: "session-a-image", mime: "image/svg+xml" };
const SESSION_B_IMAGE: MediaEntry = { ref: "session-b-image", mime: "image/svg+xml" };
let finishSessionB: (() => void) | null = null;

const api = {
  listMedia(sessionId: string) {
    if (sessionId === "session-a") {
      return Promise.resolve({ session_id: sessionId, media: [SESSION_A_IMAGE] });
    }
    return new Promise<{ session_id: string; media: MediaEntry[] }>((resolve) => {
      finishSessionB = () => resolve({ session_id: sessionId, media: [SESSION_B_IMAGE] });
    });
  },
  async request() {
    return new Response(
      new Blob(["<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12'><rect width='12' height='12' fill='blue'/></svg>"], {
        type: "image/svg+xml",
      }),
      { status: 200 },
    );
  },
} as unknown as EngineApi;

function SessionMedia({ sessionId }: { sessionId: string }) {
  const media = useSessionMedia();
  const openViewer = useMediaViewer();
  const [sawPriorSessionMedia, setSawPriorSessionMedia] = useState(false);
  useLayoutEffect(() => {
    if (sessionId === "session-b" && media?.some((item) => item.ref === SESSION_A_IMAGE.ref)) {
      setSawPriorSessionMedia(true);
    }
  }, [media, sessionId]);

  return (
    <>
      <output data-testid="session-media" data-leaked={sawPriorSessionMedia ? "yes" : "no"}>
        {media?.map((item) => item.ref).join(",") ?? "loading"}
      </output>
      {media?.map((item, index) => (
        <MediaThumb
          key={item.ref}
          ref_={item.ref}
          onOpen={openViewer ? () => openViewer(media, index) : undefined}
        />
      ))}
    </>
  );
}

function Fixture() {
  const [sessionId, setSessionId] = useState("session-a");
  return (
    <div data-active-session={sessionId}>
      <button type="button" onClick={() => setSessionId("session-b")}>Switch session</button>
      <button type="button" onClick={() => finishSessionB?.()}>Load session B media</button>
      <MediaImageProvider api={api}>
        <MediaSessionProvider api={api} sessionId={sessionId}>
          <MediaViewerProvider>
            <SessionMedia sessionId={sessionId} />
          </MediaViewerProvider>
        </MediaSessionProvider>
      </MediaImageProvider>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<Fixture />);
