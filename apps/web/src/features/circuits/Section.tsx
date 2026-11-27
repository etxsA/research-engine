import { useId, type ReactNode } from "react";
import { cn } from "@/shared/lib/utils";
import { Card, CardContent, CardHeader } from "@/shared/ui/card";

/** A titled block of the screen: a labelled region with a level-2 heading. */
export function Section({
  title,
  description,
  id,
  className,
  action,
  children,
}: {
  title: string;
  description?: string | undefined;
  id?: string | undefined;
  className?: string | undefined;
  action?: ReactNode;
  children: ReactNode;
}) {
  const headingId = useId();
  return (
    <section id={id} aria-labelledby={headingId} className={cn("min-w-0", className)}>
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 id={headingId} tabIndex={-1} className="text-base leading-snug font-medium">
              {title}
            </h2>
            {action}
          </div>
          {description ? (
            <p className="max-w-prose text-sm text-muted-foreground">{description}</p>
          ) : null}
        </CardHeader>
        <CardContent className="grid grid-cols-[minmax(0,1fr)] gap-4">{children}</CardContent>
      </Card>
    </section>
  );
}
