import type { RoomLike } from "./room-controller";

/**
 * The real LiveKit room, loaded on demand so the SDK stays out of the main
 * bundle until someone clicks the voice button. Remote audio (the assistant's
 * voice) is attached to hidden audio elements.
 */
export async function createLiveKitRoom(): Promise<RoomLike> {
  const { Room, RoomEvent, Track } = await import("livekit-client");
  const room = new Room({ adaptiveStream: false, dynacast: false });
  const audio = new Set<HTMLMediaElement>();
  room.on(RoomEvent.TrackSubscribed, (track) => {
    if (track.kind !== Track.Kind.Audio) return;
    const el = track.attach();
    audio.add(el);
    document.body.appendChild(el);
  });
  room.on(RoomEvent.TrackUnsubscribed, (track) => {
    track.detach().forEach((el) => {
      audio.delete(el);
      el.remove();
    });
  });
  return {
    connect: (url, token) => room.connect(url, token),
    setMicrophoneEnabled: (on) => room.localParticipant.setMicrophoneEnabled(on),
    disconnect() {
      audio.forEach((el) => el.remove());
      audio.clear();
      return room.disconnect();
    },
    onLocalSpeaking: (cb) =>
      room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => cb(speakers.some((p) => p === room.localParticipant))),
    onDisconnected: (cb) => room.on(RoomEvent.Disconnected, cb),
  };
}
