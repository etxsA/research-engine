import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/shared/i18n";
import { playbackLevel, usePlayback } from "@/shared/voice/playback";
import { storedPetHidden, usePetPrefs } from "./hidden";
import { PET_STATES, type PetInput } from "./petState";
import { QupiPet } from "./QupiPet";
import { QUPI_LAYERS } from "./qupi/rig";

const REST = { mic: "idle", ask: null, unavailable: false } as const;

function pet(change: Partial<Omit<PetInput, "playback">> = {}, extra = {}) {
  return <QupiPet {...REST} micLevel={0} position={null} {...change} {...extra} />;
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

describe("QupiPet", () => {
  it("is decorative: the drawing is hidden from assistive technology", () => {
    render(pet());
    const images = [...stage().querySelectorAll("img")];
    expect(images.length).toBeGreaterThan(0);
    for (const image of images) expect(image).toHaveAttribute("alt", "");
    expect(screen.queryByRole("img")).toBeNull();
    expect(stage()).toHaveAttribute("data-state", "idle");
  });

  it("stacks the layers in the order of the contract, each with 1x and 2x sources", () => {
    render(pet());
    const images = [...stage().querySelectorAll<HTMLImageElement>("img.qupi-layer")];
    expect(images.map((image) => image.dataset.layer)).toEqual([...QUPI_LAYERS]);
    for (const image of images) {
      expect(image.srcset).toMatch(/\.webp 1x, .+@2x.*\.webp 2x$/);
      expect(image.getAttribute("src")).toMatch(/\.webp/);
      expect(image).toHaveAttribute("width", "120");
      expect(image).toHaveAttribute("height", "120");
      expect(image).toHaveAttribute("decoding", "async");
      expect(image.style.transformOrigin).not.toBe("");
    }
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
    expect(stage().querySelector(".qupi-l-mouth-closed")).not.toBeNull();
  });

  it("covers every state of the table", () => {
    expect(PET_STATES).toHaveLength(8);
  });

  it("shows the place in the queue on a badge while waiting", () => {
    render(pet({ ask: "queued" }, { position: 2 }));
    expect(stage()).toHaveTextContent("2");
  });

  it("follows the microphone level through a CSS variable, for the glow of the mark", () => {
    const { rerender } = render(pet({ mic: "recording" }, { micLevel: 0.25 }));
    expect(stage().parentElement!.style.getPropertyValue("--qupi-level")).toBe("0.25");
    rerender(pet({ mic: "recording" }, { micLevel: 0.75 }));
    expect(stage().parentElement!.style.getPropertyValue("--qupi-level")).toBe("0.75");
  });

  it("says the state in words, in both languages", async () => {
    const { rerender } = render(pet());
    expect(screen.getByRole("status")).toHaveTextContent("Qupi is resting.");
    rerender(pet({ ask: "answering" }));
    expect(screen.getByRole("status")).toHaveTextContent("Qupi is thinking.");
    await act(() => i18n.changeLanguage("es"));
    expect(screen.getByRole("status")).toHaveTextContent("Qupi está pensando.");
    expect(screen.getByRole("button", { name: "Ocultar a Qupi" })).toBeInTheDocument();
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
      const wrap = stage().parentElement!;
      expect(wrap.style.getPropertyValue("--qupi-mouth")).toBe("0.800");
      expect(wrap.dataset.mouth).toBe("open");
      playbackLevel.current = 0.25;
      run();
      expect(wrap.dataset.mouth).toBe("half");
      playbackLevel.current = 0.1;
      run();
      expect(wrap.style.getPropertyValue("--qupi-mouth")).toBe("0.100");
      expect(wrap.dataset.mouth).toBe("closed");
      // Just past the closed/half edge the mouth holds its shape, so it does not flicker.
      playbackLevel.current = 0.16;
      run();
      expect(wrap.dataset.mouth).toBe("closed");
      playbackLevel.current = 0.19;
      run();
      expect(wrap.dataset.mouth).toBe("half");
      act(() => usePlayback.getState().setState("idle"));
      expect(frames.size).toBe(0);
      expect(wrap.dataset.mouth).toBeUndefined();
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

    it("moves nothing: no looping extra and no frame loop", () => {
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
        { unavailable: true },
        { mic: "recording" },
      ] as const) {
        usePlayback.setState({ state: "idle" });
        rerender(pet(change));
        expect(stage().querySelector(".qupi-orbit-live, .qupi-dot, .qupi-z-live")).toBeNull();
      }
      expect(stage()).toHaveAttribute("data-state", "listening");
    });

    it("shows the voice as a mouth swap only, with no level-driven motion", () => {
      setReducedMotion(true);
      usePlayback.setState({ state: "speaking" });
      const raf = vi.spyOn(window, "requestAnimationFrame");
      render(pet());
      const wrap = stage().parentElement!;
      expect(wrap.dataset.mouth).toBe("half");
      playbackLevel.current = 0.9;
      expect(raf).not.toHaveBeenCalled();
      expect(wrap.dataset.mouth).toBe("half");
    });

    it("does not blink and plays no one-shot move", () => {
      setReducedMotion(true);
      vi.useFakeTimers();
      const animate = vi.fn();
      HTMLElement.prototype.animate = animate;
      try {
        const { rerender } = render(pet());
        act(() => void vi.advanceTimersByTime(20_000));
        expect(document.querySelector("[data-blink]")).toBeNull();
        rerender(pet({ mic: "recording" }));
        expect(animate).not.toHaveBeenCalled();
      } finally {
        vi.useRealTimers();
        delete (HTMLElement.prototype as { animate?: unknown }).animate;
      }
    });

    it("keeps the glow still while listening", () => {
      setReducedMotion(true);
      render(pet({ mic: "recording" }, { micLevel: 0.8 }));
      expect(stage().parentElement?.style.getPropertyValue("--qupi-level")).toBe("0");
    });

    it("animates when motion is welcome", () => {
      setReducedMotion(false);
      render(pet());
      expect(stage()).toHaveAttribute("data-motion", "full");
      expect(stage().querySelector(".qupi-l-body")).not.toBeNull();
    });

    it("circles two qubits around the head while thinking, and only then", () => {
      setReducedMotion(false);
      const { rerender } = render(pet({ ask: "retrieving" }));
      expect(stage().querySelectorAll(".qupi-orbit-live")).toHaveLength(2);
      rerender(pet());
      expect(stage().querySelector(".qupi-orbit")).toBeNull();
    });
  });

  describe("the blink", () => {
    const lids = () => stage().querySelector<HTMLElement>(".qupi-l-eyelids")!;
    beforeEach(() => vi.useFakeTimers());
    afterEach(() => vi.useRealTimers());
    const advance = (ms: number) => act(() => void vi.advanceTimersByTime(ms));

    it("closes the eyelids for a moment every 3 to 6 seconds while resting", () => {
      vi.spyOn(Math, "random").mockReturnValue(0);
      render(pet());
      advance(2900);
      expect(lids().dataset.blink).toBeUndefined();
      advance(150);
      expect(lids().dataset.blink).toBe("true");
      advance(150);
      expect(lids().dataset.blink).toBeUndefined();
      advance(3000);
      expect(lids().dataset.blink).toBe("true");
    });

    it("waits up to 6 seconds at most", () => {
      vi.spyOn(Math, "random").mockReturnValue(0.999);
      render(pet());
      advance(5900);
      expect(lids().dataset.blink).toBeUndefined();
      advance(200);
      expect(lids().dataset.blink).toBe("true");
    });

    it("blinks slowly and rarely while waiting", () => {
      vi.spyOn(Math, "random").mockReturnValue(0);
      render(pet({ ask: "queued" }));
      advance(4900);
      expect(lids().dataset.blink).toBeUndefined();
      advance(200);
      expect(lids().dataset.blink).toBe("true");
      advance(200);
      expect(lids().dataset.blink).toBe("true");
      advance(300);
      expect(lids().dataset.blink).toBeUndefined();
    });

    it("blinks in every state with open eyes, restarts on a change, and never when asleep", () => {
      vi.spyOn(Math, "random").mockReturnValue(0);
      const { rerender } = render(pet({ ask: "retrieving" }));
      advance(3100);
      expect(lids().dataset.blink).toBe("true");
      rerender(pet({ mic: "recording" }));
      expect(lids().dataset.blink).toBeUndefined();
      advance(3100);
      expect(lids().dataset.blink).toBe("true");
      usePlayback.setState({ state: "speaking" });
      rerender(pet());
      expect(lids().dataset.blink).toBeUndefined();
      advance(3100);
      expect(lids().dataset.blink).toBe("true");
      usePlayback.setState({ state: "idle" });
      rerender(pet({ unavailable: true }));
      expect(lids().dataset.blink).toBeUndefined();
      advance(20_000);
      expect(lids().dataset.blink).toBeUndefined();
    });

    it("stops the timer when the pet is hidden or goes away", () => {
      vi.spyOn(Math, "random").mockReturnValue(0);
      const { unmount } = render(pet());
      unmount();
      expect(vi.getTimerCount()).toBe(0);
    });
  });

  describe("moves on a change of state", () => {
    const animate = vi.fn();
    beforeEach(() => {
      animate.mockClear();
      HTMLElement.prototype.animate = animate;
    });
    afterEach(() => {
      delete (HTMLElement.prototype as { animate?: unknown }).animate;
    });

    it("plays nothing when it first appears", () => {
      render(pet({ mic: "recording" }));
      expect(animate).not.toHaveBeenCalled();
    });

    it("squashes the whole body and flicks both ears when it starts to listen", () => {
      const { rerender } = render(pet());
      rerender(pet({ mic: "recording" }));
      const targets = (animate.mock.contexts as HTMLElement[]).map((node) => node.className);
      expect(targets).toHaveLength(3);
      expect(targets.some((name) => name.includes("qupi-rig"))).toBe(true);
      expect(targets.some((name) => name.includes("qupi-l-ear-left"))).toBe(true);
      expect(targets.some((name) => name.includes("qupi-l-ear-right"))).toBe(true);
    });

    it("shakes once, added to the squash, for an error", () => {
      const { rerender } = render(pet());
      rerender(pet({ ask: "failed" }));
      expect(animate).toHaveBeenCalledTimes(2);
      const options = animate.mock.calls.map((call) => call[1] as KeyframeAnimationOptions);
      expect(options.find((option) => option.duration === 480)).toMatchObject({ composite: "add" });
    });

    it("does not repeat a move while the state stays the same", () => {
      const { rerender } = render(pet());
      rerender(pet({ ask: "retrieving" }));
      animate.mockClear();
      rerender(pet({ ask: "retrieving" }, { position: 3 }));
      expect(animate).not.toHaveBeenCalled();
    });
  });

  describe("Hide Qupi", () => {
    it("hides it, remembers the choice, and shows it again", async () => {
      const user = userEvent.setup();
      render(pet());
      await user.click(screen.getByRole("button", { name: "Hide Qupi" }));
      expect(document.querySelector("[data-pet]")).toBeNull();
      expect(screen.queryByRole("status")).toBeNull();
      expect(window.localStorage.getItem("re.pet.hidden")).toBe("true");
      expect(storedPetHidden()).toBe(true);
      await user.click(screen.getByRole("button", { name: "Show Qupi" }));
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
      await userEvent.setup().click(screen.getByRole("button", { name: "Hide Qupi" }));
      expect(screen.getByRole("button", { name: "Show Qupi" })).toBeInTheDocument();
    });
  });
});
