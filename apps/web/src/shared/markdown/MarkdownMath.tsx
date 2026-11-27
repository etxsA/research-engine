import "katex/dist/katex.min.css";
import {
  type ComponentProps,
  createContext,
  type ReactNode,
  use,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import Markdown, { type Components } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { cn } from "@/shared/lib/utils";
import { closeDisplayMathOnItsOwnLine } from "./displayMath";
import { remarkPlainFootnotes } from "./plainFootnotes";
import { escapeBarsInTableMath } from "./tableMath";

/**
 * KaTeX renders the formulas to HTML plus MathML (the MathML is what a screen reader reads).
 * `trust: false` keeps `\href`, `\url` and similar commands out; a formula it cannot read is
 * shown as typed instead of throwing.
 */
const KATEX_OPTIONS = {
  throwOnError: false,
  strict: "ignore",
  trust: false,
  output: "htmlAndMathml",
  // Sizes a formula asks for (`\rule{1000em}{1000em}`) stop at 10 em, so text cannot cover the page.
  maxSize: 10,
  // Papers copy `\label{eq:1}` into their formulas and KaTeX has no `\label`: it takes its one
  // argument and shows nothing (`\@secondoftwo` is KaTeX's own, and needs no `trust`).
  // A getter, so each formula gets a macros object of its own: KaTeX writes the `\gdef` of a
  // formula into it, and a shared one would carry that to every later formula.
  get macros() {
    return { "\\label": "\\@secondoftwo{#1}{}" };
  },
} as const;

/**
 * GFM brings pipe tables (plus strikethrough, task lists and bare web addresses).
 * `singleTilde: false` keeps a lone `~` (an approximation in text or in a formula) from striking
 * text through; only `~~double~~` does. Its footnotes are turned back into the text they were
 * (see `plainFootnotes.ts`).
 */
const REMARK_PLUGINS = [
  remarkMath,
  [remarkGfm, { singleTilde: false }],
  remarkPlainFootnotes,
] as ComponentProps<typeof Markdown>["remarkPlugins"];
const REHYPE_PLUGINS = [[rehypeKatex, KATEX_OPTIONS]] as ComponentProps<
  typeof Markdown
>["rehypePlugins"];

/** Only web addresses become links; anything else (a relative path, `javascript:`) is plain text. */
export function isWebUrl(href: string | undefined): href is string {
  if (href === undefined) return false;
  try {
    const url = new URL(href);
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
}

export function SafeLink({ href, children }: { href?: string | undefined; children?: ReactNode }) {
  if (!isWebUrl(href)) return <span>{children}</span>;
  return (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  );
}

/** A paper may point at an image on disk or on the web. Neither is loaded: the alt text stands in. */
function NoImage({ alt }: { alt?: string | undefined }) {
  return alt ? <span className="italic">[{alt}]</span> : null;
}

/** Headings of a paper start below the page heading of the screen that shows them. */
const heading = (level: 3 | 4 | 5 | 6) => {
  const Tag = `h${level}` as const;
  return function Heading({ children }: { children?: ReactNode }) {
    return <Tag>{children}</Tag>;
  };
};

/**
 * A formula inside a line cannot wrap, so one wider than its line would widen the page. When
 * that happens it scrolls inside its own box, which then takes the keyboard focus so it can be
 * read. Each formula is measured by itself: a short one beside a wide one stays plain text, and
 * a box goes back to plain when the line becomes wide enough. KaTeX's own spans carry their
 * layout as inline styles and attributes, which are kept.
 */
function KatexSpan({ children, ...rest }: ComponentProps<"span">) {
  const own = useRef<HTMLSpanElement>(null);
  const [scrolls, setScrolls] = useState(false);
  useLayoutEffect(() => {
    const formula = own.current;
    if (!formula || formula.parentElement?.classList.contains("katex-display")) return;
    const line = formula.closest("p, li, td, th, h3, h4, h5, h6, blockquote, div");
    if (!line || typeof ResizeObserver === "undefined") return;
    const measure = () => {
      const style = getComputedStyle(line);
      const padding =
        (Number.parseFloat(style.paddingLeft) || 0) + (Number.parseFloat(style.paddingRight) || 0);
      // An inline formula breaks between its parts, so one that overflows has a line of its own
      // wider than the room; once it is a scroll box, its scroll width says the same. The mark
      // therefore does not change what is measured. A pixel of slack keeps rounding from flipping it.
      const needed = Math.max(
        formula.scrollWidth,
        ...Array.from(formula.getClientRects(), (rect) => rect.width),
      );
      setScrolls(needed > line.clientWidth - padding + 1);
    };
    measure();
    const watcher = new ResizeObserver(measure);
    watcher.observe(line);
    return () => watcher.disconnect();
  }, []);
  return (
    <span
      {...rest}
      ref={own}
      className={cn("katex", scrolls && "math-scroll")}
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
      tabIndex={scrolls ? 0 : undefined}
    >
      {children}
    </span>
  );
}

/**
 * A table cannot wrap below the width of its longest word, so a wide table would widen the page.
 * It sits in a box of its own that scrolls sideways, and the box takes the keyboard focus only
 * while there is something to scroll.
 */
function ScrollTable({ children }: { children?: ReactNode }) {
  const own = useRef<HTMLDivElement>(null);
  const [scrolls, setScrolls] = useState(false);
  useLayoutEffect(() => {
    const box = own.current;
    if (!box || typeof ResizeObserver === "undefined") return;
    const measure = () => setScrolls(box.scrollWidth > box.clientWidth + 1);
    measure();
    const watcher = new ResizeObserver(measure);
    // The box does not follow its table (it is size-contained), so the table is watched too.
    watcher.observe(box);
    if (box.firstElementChild) watcher.observe(box.firstElementChild);
    return () => watcher.disconnect();
  }, []);
  return (
    <div
      ref={own}
      className="table-scroll"
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
      tabIndex={scrolls ? 0 : undefined}
    >
      <table>{children}</table>
    </div>
  );
}

/** What to show under a fenced `latex` block, given its source; see `MarkdownMath`. */
export type LatexBlockAction = (source: string) => ReactNode;

// A context, not a component made for each caller: react-markdown takes components as element
// types, and a new function on every render would draw every code block again.
const LatexBlockActionContext = createContext<LatexBlockAction | undefined>(undefined);

interface HastNode {
  type: string;
  tagName?: string;
  value?: string;
  properties?: { className?: unknown };
  children?: HastNode[];
}

const textOf = (node: HastNode): string =>
  node.type === "text" ? (node.value ?? "") : (node.children ?? []).map(textOf).join("");

/**
 * The source of a fenced `latex` block (```latex), or null for any other block. Only a fenced
 * block counts: inline code is a `code` element outside a `pre`, and never reaches here.
 */
function latexSource(pre: HastNode | undefined): string | null {
  const code = pre?.children?.find((child) => child.type === "element");
  const classes = code?.properties?.className;
  if (code?.tagName !== "code" || !Array.isArray(classes) || !classes.includes("language-latex")) {
    return null;
  }
  // The fence's last line break belongs to the fence, not to the circuit.
  return textOf(code).replace(/\n$/, "");
}

function CodeBlock({ node, children, ...rest }: ComponentProps<"pre"> & { node?: unknown }) {
  const action = use(LatexBlockActionContext);
  const source = action ? latexSource(node as HastNode | undefined) : null;
  const block = <pre {...rest}>{children}</pre>;
  if (!action || source === null) return block;
  return (
    <div className="grid gap-2">
      {block}
      <div>{action(source)}</div>
    </div>
  );
}

const SAFE_COMPONENTS: Components = {
  table: ({ children }) => <ScrollTable>{children}</ScrollTable>,
  pre: CodeBlock,
  // react-markdown also hands the syntax node to a custom component; left in `rest` it would
  // reach the DOM as `node="[object Object]"`.
  span: ({ className, children, node, ...rest }) => {
    void node;
    return className === "katex" ? (
      <KatexSpan {...rest}>{children}</KatexSpan>
    ) : (
      <span className={className} {...rest}>
        {children}
      </span>
    );
  },
  a: ({ href, children }) => <SafeLink href={href}>{children}</SafeLink>,
  img: ({ alt }) => <NoImage alt={alt} />,
  h1: heading(3),
  h2: heading(4),
  h3: heading(5),
  h4: heading(6),
  h5: heading(6),
  h6: heading(6),
};

export interface MarkdownMathProps {
  children: string;
  /** Replaces the safe default of an element (the answer view turns citations into buttons). */
  components?: Components;
  className?: string;
  /**
   * Shown under each fenced `latex` code block, with the block's source (docingest fences only
   * circuits as `latex`). Not applied to code in other languages or to inline code.
   */
  latexBlockAction?: LatexBlockAction | undefined;
}

/**
 * Markdown with LaTeX math for answers and papers. Raw HTML in the text is never rendered: it
 * stays as the text it was written as (there is no `rehype-raw`), so `a <b and c> d` reads as
 * typed. Links open only for web addresses, and no image is fetched.
 */
export function MarkdownMath({
  children,
  components,
  className,
  latexBlockAction,
}: MarkdownMathProps) {
  return (
    <div className={cn("markdown", className)}>
      <LatexBlockActionContext value={latexBlockAction}>
        <Markdown
          remarkPlugins={REMARK_PLUGINS}
          rehypePlugins={REHYPE_PLUGINS}
          components={{ ...SAFE_COMPONENTS, ...components }}
        >
          {escapeBarsInTableMath(closeDisplayMathOnItsOwnLine(children))}
        </Markdown>
      </LatexBlockActionContext>
    </div>
  );
}
