import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { useCircuitStore, type NumberedAnnouncement } from "./store";

/**
 * One short sentence for screen readers when a reading or an answer arrives. The tables and lists
 * are not live regions: announcing 64 state rows or 256 matrix cells would drown the sentence.
 * Refusals and missing values are announced by their own alerts.
 *
 * The sentence comes from the last announcement made by the flows, not from the current state: a
 * person editing the screen clears the answer, and that must not announce anything. Two regions
 * take turns, because a region is announced when text is added to it, and the same words twice
 * in one region would change nothing. Both are always on the page, because a live region is
 * announced reliably only when it exists before its text changes.
 */
export function LiveStatus() {
  const { t } = useTranslation();
  const announcement = useCircuitStore((state) => state.announcement);
  const turn = announcement ? announcement.serial % 2 : 0;
  return (
    <div data-testid="live-status" className="sr-only">
      {[0, 1].map((slot) => (
        <p key={slot} role="status">
          {announcement && slot === turn ? sentence(announcement, t) : ""}
        </p>
      ))}
    </div>
  );
}

function sentence(announcement: NumberedAnnouncement, t: TFunction) {
  switch (announcement.kind) {
    case "reading":
      return t("circuits.reading.live", {
        status: t(`circuits.reading.liveStatus.${announcement.status}`),
        found: announcement.found,
      });
    case "run":
      return t("circuits.result.live.run");
    case "check":
      return t("circuits.result.live.check");
    case "equal":
      return t("circuits.result.live.equal");
    case "notEqual":
      return t("circuits.result.live.notEqual");
    case "qasm":
      return t("circuits.export.live", { programs: announcement.programs });
    case "paperCircuits":
      return t("circuits.paper.live.circuits", { blocks: announcement.blocks });
    case "paperQasm":
      return t("circuits.paper.live.export", {
        programs: announcement.programs,
        refused: announcement.refused,
      });
    case "copy":
      return t(`circuits.export.copied.${announcement.outcome}`, { file: announcement.file });
  }
}
