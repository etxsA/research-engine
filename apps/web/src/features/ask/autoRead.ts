import { useSyncExternalStore } from "react";
import { readStored, writeStored } from "@/shared/lib/storage";

/** Whether an answer to a spoken question is read aloud by itself. On unless the person turned it off. */
export const AUTO_READ_KEY = "re.voice.autoRead";

const listeners = new Set<() => void>();

export function getAutoRead(): boolean {
  return readStored(AUTO_READ_KEY) !== "false";
}

export function setAutoRead(on: boolean): void {
  writeStored(AUTO_READ_KEY, String(on));
  for (const listener of [...listeners]) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useAutoReadSetting(): [boolean, (on: boolean) => void] {
  return [useSyncExternalStore(subscribe, getAutoRead), setAutoRead];
}
