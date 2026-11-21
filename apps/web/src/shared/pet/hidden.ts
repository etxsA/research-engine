import { create } from "zustand";
import { readStored, writeStored } from "@/shared/lib/storage";

export const PET_HIDDEN_KEY = "re.pet.hidden";

export function storedPetHidden(): boolean {
  return readStored(PET_HIDDEN_KEY) === "true";
}

interface PetPrefs {
  hidden: boolean;
  setHidden: (hidden: boolean) => void;
}

export const usePetPrefs = create<PetPrefs>()((set) => ({
  hidden: storedPetHidden(),
  setHidden: (hidden) => {
    writeStored(PET_HIDDEN_KEY, String(hidden));
    set({ hidden });
  },
}));
