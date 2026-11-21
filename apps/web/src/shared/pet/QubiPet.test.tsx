import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/shared/i18n";
import { playbackLevel, usePlayback } from "@/shared/voice/playback";
import { storedPetHidden, usePetPrefs } from "./hidden";
import { PET_STATES, type PetInput } from "./petState";
import { QubiPet } from "./QubiPet";

const REST = { mic: "idle", ask: null, unavailable: false } as const;

function pet(change: Partial<Omit<PetInput, "playback">> = {}, extra = {}) {
  return <QubiPet {...REST} micLevel={0} position={null} {...change} {...extra} />;
}
const stage = () => document.querySelector<HTMLElement>("[data-pet]")!;

function setReducedMotion(reduced: boolean) {
  const original = window.matchMedia.bind(window);
  window.matchMedia = (query: string) => {
    const base = original(query);
    return query.includes("reduced-motion") ? { ...base, matches: reduced } : base;
  };
}

beforeEach(() => {
  usePetPrefs.setState({ hidden: false });
  usePlayback.setState({ state: "idle" });
  playbackLevel.current = 0;
});
afterEach(() => vi.restoreAllMocks());

describe("QubiPet", () => {
  it("is decorative: the drawing is hidden from assistive technology", () => {
    render(pet());
    expect(stage().querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(stage()).toHaveAttribute("data-state", "idle");
  });

  it.each<[string, Partial<Omit<PetInput, "playback">>, string]>([
    ["idle", {}, "idle"],
    ["listening", { mic: "recording" }, "listening"],
    ["transcribing", { mic: "transcribing" }, "transcribing"],
    ["waiting", { ask: "queued" }, "waiting"],
    ["thinking", { ask: "retrieving" }, "thinking"],
    ["error", { ask: "failed" }, "error"],
    ["unavailable", { unavailable: true }, "unavailable"],
  ])("draws the %s state", (_name, change, state) => {
    render(pet(change));
    expect(stage()).toHaveAttribute("data-state", state);
  });

  it("draws the speaking state when the reader speaks", () => {
    render(pet());
    act(() => usePlayback.getState().setState("speaking"));
    expect(stage()).toHaveAttribute("data-state", "speaking");
    expect(stage().querySelector(".qubi-mouth")).not.toBeNull();
  });

  it("covers every state of the table", () => {
    expect(PET_STATES).toHaveLength(8);
  });

  it("shows the place in the queue on a badge while waiting", () => {
    render(pet({ ask: "queued" }, { position: 2 }));
    expect(stage()).toHaveTextContent("2");
  });

  it("scales the rings with the microphone level through a CSS variable", () => {
    const { rerender } = render(pet({ mic: "recording" }, { micLevel: 0.25 }));
    expect(stage().parentElement!.style.getPropertyValue("--qubi-level")).toBe("0.25");
    rerender(pet({ mic: "recording" }, { micLevel: 0.75 }));
    expect(stage().parentElement!.style.getPropertyValue("--qubi-level")).toBe("0.75");
  });

  it("says the state in words, in both languages", async () => {
    const { rerender } = render(pet());
    expect(screen.getByRole("status")).toHaveTextContent("Qubi is resting.");
    rerender(pet({ ask: "answering" }));
    expect(screen.getByRole("status")).toHaveTextContent("Qubi is thinking.");
    await act(() => i18n.changeLanguage("es"));
    expect(screen.getByRole("status")).toHaveTextContent("Qubi está pensando.");
    expect(screen.getByRole("button", { name: "Ocultar a Qubi" })).toBeInTheDocument();
  });

  describe("the mouth while speaking", () => {
    it("follows the playback level, frame by frame, with no re-render", () => {
      let next = 0;
      const frames = new Map<number, FrameRequestCallback>();
      vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
        frames.set(++next, callback);
        return next;
      });
      vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => void frames.delete(id));
      render(pet());
      act(() => usePlayback.getState().setState("speaking"));
      const run = () => {
        const [id, callback] = [...frames][0]!;
        frames.delete(id);
        callback(0);
      };
      playbackLevel.current = 0.8;
      run();
      expect(stage().parentElement!.style.getPropertyValue("--qubi-mouth")).toBe("0.800");
      playbackLevel.current = 0.1;
      run();
      expect(stage().parentElement!.style.getPropertyValue("--qubi-mouth")).toBe("0.100");
      act(() => usePlayback.getState().setState("idle"));
      expect(frames.size).toBe(0);
    });

    it("stops the loop when the pet goes away", () => {
      const cancel = vi.spyOn(window, "cancelAnimationFrame");
      vi.spyOn(window, "requestAnimationFrame").mockReturnValue(7);
      usePlayback.setState({ state: "speaking" });
      const { unmount } = render(pet());
      unmount();
      expect(cancel).toHaveBeenCalledWith(7);
    });
  });

  describe("reduced motion", () => {
    afterEach(() => setReducedMotion(false));

    it("moves nothing: no animation class and no frame loop", () => {
      setReducedMotion(true);
      const raf = vi.spyOn(window, "requestAnimationFrame");
      usePlayback.setState({ state: "speaking" });
      const { rerender } = render(pet());
      expect(stage()).toHaveAttribute("data-motion", "still");
      expect(raf).not.toHaveBeenCalled();
      for (const change of [
        {},
        { ask: "retrieving" },
        { mic: "transcribing" },
        { mic: "recording" },
      ] as const) {
        usePlayback.setState({ state: "idle" });
        rerender(pet(change));
        expect(
          stage().querySelector("[class*='qubi-bob'], .qubi-antenna-precess, .qubi-dot"),
        ).toBeNull();
      }
      expect(stage()).toHaveAttribute("data-state", "listening");
    });

    it("keeps the rings still while listening", () => {
      setReducedMotion(true);
      render(pet({ mic: "recording" }, { micLevel: 0.8 }));
      expect(stage().parentElement?.style.getPropertyValue("--qubi-level")).toBe("0");
    });

    it("animates when motion is welcome", () => {
      setReducedMotion(false);
      render(pet());
      expect(stage()).toHaveAttribute("data-motion", "full");
      expect(stage().querySelector(".qubi-bob")).not.toBeNull();
    });
  });

  describe("Hide Qubi", () => {
    it("hides it, remembers the choice, and shows it again", async () => {
      const user = userEvent.setup();
      render(pet());
      await user.click(screen.getByRole("button", { name: "Hide Qubi" }));
      expect(document.querySelector("[data-pet]")).toBeNull();
      expect(screen.queryByRole("status")).toBeNull();
      expect(window.localStorage.getItem("re.pet.hidden")).toBe("true");
      expect(storedPetHidden()).toBe(true);
      await user.click(screen.getByRole("button", { name: "Show Qubi" }));
      expect(stage()).toBeInTheDocument();
      expect(window.localStorage.getItem("re.pet.hidden")).toBe("false");
      expect(storedPetHidden()).toBe(false);
    });

    it("starts hidden when the stored choice says so", () => {
      window.localStorage.setItem("re.pet.hidden", "true");
      expect(storedPetHidden()).toBe(true);
    });

    it("keeps working when storage refuses writes", async () => {
      vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new Error("quota");
      });
      render(pet());
      await userEvent.setup().click(screen.getByRole("button", { name: "Hide Qubi" }));
      expect(screen.getByRole("button", { name: "Show Qubi" })).toBeInTheDocument();
    });
  });
});
