import { CircleAlert } from "lucide-react";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Alert, AlertDescription, AlertTitle } from "@/shared/ui/alert";
import { useCircuitStore, type Problem } from "./store";

type Translate = ReturnType<typeof useTranslation>["t"];

function apiTitle(status: number, t: Translate): string {
  switch (status) {
    case 400:
      return t("circuits.problem.badRequest");
    case 404:
      return t("circuits.problem.notFound");
    case 413:
      return t("circuits.problem.tooLarge");
    case 422:
      return t("circuits.problem.unprocessable");
    case 503:
      return t("circuits.problem.busy");
    case 401:
    case 403:
      return t("circuits.problem.notAllowed");
    default:
      return t("circuits.problem.failed");
  }
}

/** The message of a problem that stopped a request or its answer. The server's own words are kept. */
export function ProblemView({ problem }: { problem: Problem }) {
  const { t } = useTranslation();
  let title: string;
  let detail: string | null = null;
  let paths: { path: string; message: string }[] = [];
  let retry: number | undefined;
  if (problem.kind === "api") {
    title = apiTitle(problem.error.status, t);
    detail = problem.error.detail;
    paths = problem.error.errors.filter((field) => field.message !== problem.error.detail);
    retry = problem.error.retryAfter;
  } else if (problem.kind === "malformed") {
    title = t("circuits.problem.malformed");
  } else {
    title = t(`circuits.local.${problem.code}.title`);
    detail = t(`circuits.local.${problem.code}.detail`, problem.params);
  }
  return (
    <Alert variant="destructive" data-testid="problem">
      <CircleAlert aria-hidden="true" />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription className="grid gap-1">
        {detail ? <p>{detail}</p> : null}
        {paths.length > 0 ? (
          <ul className="list-disc pl-5">
            {paths.map((field) => (
              <li key={`${field.path}:${field.message}`}>
                <code>{field.path}</code> {field.message}
              </li>
            ))}
          </ul>
        ) : null}
        {retry !== undefined ? (
          <p>{t("circuits.problem.retry", { seconds: Math.ceil(retry) })}</p>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}

export function CurrentProblem() {
  const problem = useCircuitStore((state) => state.problem);
  const ref = useRef<HTMLDivElement>(null);
  // The problem can appear far from the button that caused it: bring it into view.
  useEffect(() => {
    if (problem) ref.current?.scrollIntoView({ block: "nearest" });
  }, [problem]);
  return problem ? (
    <div ref={ref}>
      <ProblemView problem={problem} />
    </div>
  ) : null;
}
