import { Cpu } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { Button } from "@/shared/ui/button";

/**
 * Links from the Ask screen to the Circuits screen. A feature never imports another one, so these
 * carry only a URL and router state: the LaTeX of a block in `state.latex` (it can be long, and it
 * is paper text, so it stays out of the address), or a paper's id in `?doc=`.
 */

/** "Open in Circuits" under a fenced `latex` block of an answer or of a paper's page. */
export function OpenInCircuits({ latex }: { latex: string }) {
  const { t } = useTranslation(undefined, { keyPrefix: "ask" });
  return (
    <Button asChild variant="outline" size="sm">
      <Link to="/circuits" state={{ latex }}>
        <Cpu aria-hidden="true" />
        {t("circuits.open")}
      </Link>
    </Button>
  );
}

/** The `latexBlockAction` of `MarkdownMath` for the Ask screen. A module function, so it never changes. */
export function openInCircuits(latex: string) {
  return <OpenInCircuits latex={latex} />;
}

/** "Circuits of this paper": the Paper tab of the Circuits screen, on this paper (its full id). */
export function PaperCircuitsLink({ docId }: { docId: string }) {
  const { t } = useTranslation(undefined, { keyPrefix: "ask" });
  return (
    <Link
      to={`/circuits?doc=${encodeURIComponent(docId)}`}
      className="w-fit text-sm underline underline-offset-3"
    >
      {t("circuits.paper")}
    </Link>
  );
}
