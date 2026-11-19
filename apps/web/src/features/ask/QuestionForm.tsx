import { useId, type FormEvent, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/shared/ui/button";
import { Label } from "@/shared/ui/label";
import { Textarea } from "@/shared/ui/textarea";
import { isBlank } from "./flow";
import { MicButton } from "./MicButton";
import { SettingsPopover } from "./SettingsPopover";
import { useAskStore } from "./store";

export const MAX_QUESTION_CHARS = 4000; // the API's limit

interface QuestionFormProps {
  /** The engine is starting or down, or a question is being answered: Ask is off. */
  blocked: boolean;
  /** A question is being answered; the button says so. */
  busy: boolean;
  /** The engine reports a problem that may be fixed already: a second button sends regardless. */
  askAnyway: boolean;
  /** `/status` says speech to text is not set up: the mic button is off. */
  speechUnavailable?: boolean;
  onSubmit: (question: string) => void;
}

/** The question box. Ctrl+Enter (or Cmd+Enter) sends; a blank question cannot be sent. */
export function QuestionForm({
  blocked,
  busy,
  askAnyway,
  speechUnavailable = false,
  onSubmit,
}: QuestionFormProps) {
  const { t, i18n } = useTranslation(undefined, { keyPrefix: "ask" });
  const question = useAskStore((state) => state.question);
  const setQuestion = useAskStore((state) => state.setQuestion);
  const id = useId();
  const blank = isBlank(question);
  const canSend = !blank && !blocked;

  const canForce = askAnyway && !blank && !busy;

  // The box empties once the question is sent, so the next one starts from nothing. The question
  // stays in the history, and "Edit and ask again" puts it back.
  const submit = () => {
    onSubmit(question);
    setQuestion("");
  };
  const send = () => {
    if (canSend) submit();
  };
  // The transcript goes after what is already there, for the person to edit; it is never sent.
  const addTranscript = (text: string) => {
    const current = useAskStore.getState().question;
    const joined = current.trim() === "" ? text : `${current.trimEnd()} ${text}`;
    setQuestion(joined.slice(0, MAX_QUESTION_CHARS));
    document.getElementById("ask-question")?.focus();
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      send();
    }
  };
  const onFormSubmit = (event: FormEvent) => {
    event.preventDefault();
    send();
  };
  const counter = new Intl.NumberFormat(i18n.language);

  return (
    <form onSubmit={onFormSubmit} className="grid gap-3" noValidate>
      <div className="grid gap-1.5">
        <Label htmlFor="ask-question">{t("form.label")}</Label>
        <Textarea
          id="ask-question"
          name="question"
          rows={4}
          className="min-h-24"
          maxLength={MAX_QUESTION_CHARS}
          value={question}
          placeholder={t("form.placeholder")}
          aria-describedby={`${id}-hint ${id}-counter ${id}-why`}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
          <p id={`${id}-hint`}>{t("form.hint", { max: counter.format(MAX_QUESTION_CHARS) })}</p>
          <p id={`${id}-counter`}>
            {t("form.counter", {
              count: counter.format(question.length),
              max: counter.format(MAX_QUESTION_CHARS),
            })}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" disabled={!canSend} aria-describedby={`${id}-why`}>
          {busy ? t("form.sending") : t("form.submit")}
        </Button>
        {askAnyway ? (
          <Button type="button" variant="outline" disabled={!canForce} onClick={submit}>
            {t("form.askAnyway")}
          </Button>
        ) : null}
        <SettingsPopover disabled={busy} />
        <MicButton unavailable={speechUnavailable} onText={addTranscript} />
        <p id={`${id}-why`} className="text-xs text-muted-foreground">
          {busy
            ? t("form.busy")
            : blank
              ? t("form.blank")
              : askAnyway
                ? t("form.engineDown")
                : blocked
                  ? t("form.starting")
                  : null}
        </p>
      </div>
    </form>
  );
}
