import { create } from 'zustand';

/** What the answer reader is doing, for the pet and anything else that reacts to speech. */
export type PlaybackState = 'idle' | 'speaking';

interface PlaybackStore {
  state: PlaybackState;
  setState: (state: PlaybackState) => void;
}

export const usePlayback = create<PlaybackStore>((set) => ({
  state: 'idle',
  setState: (state) => set({ state }),
}));

/** Loudness of the audio being played, 0 to 1, written every frame without re-rendering React. */
export const playbackLevel = { current: 0 };
