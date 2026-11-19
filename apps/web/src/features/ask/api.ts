import { keepPreviousData, useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";
import { $api, fetchClient } from "@/shared/api/client";
import { createAskFlow, type AskFlow } from "./flow";
import type { EventSourceFactory } from "./stream";
import type { AskRequest } from "./types";

const STATUS = "/api/v1/status";
const ASKS = "/api/v1/asks";
const ASK = "/api/v1/asks/{ask_id}";

const NORMAL_REFRESH_MS = 30_000;
/** While the engine starts, look again soon, so the form opens as soon as it can. */
const WARMING_REFRESH_MS = 3_000;

/**
 * The service status. The top bar reads the same endpoint with the same key, so one request
 * serves both; here it is also read faster while the engine is warming up.
 */
export function useEngineStatus() {
  return $api.useQuery(
    "get",
    STATUS,
    {},
    {
      retry: false,
      refetchInterval: (query) =>
        query.state.data?.ask.state === "warming_up" ? WARMING_REFRESH_MS : NORMAL_REFRESH_MS,
    },
  );
}

/** A paper's title, authors and pages. A paper does not change within an index generation. */
export function useDocument(docId: string | undefined) {
  return $api.useQuery(
    "get",
    "/api/v1/documents/{doc_id}",
    { params: { path: { doc_id: docId ?? "" } } },
    { enabled: docId !== undefined, staleTime: Infinity, retry: false },
  );
}

/** One page of a paper as Markdown. The previous page stays on screen while the next one loads. */
export function useDocumentPage(docId: string | undefined, page: number) {
  return $api.useQuery(
    "get",
    "/api/v1/documents/{doc_id}/pages/{page}",
    { params: { path: { doc_id: docId ?? "", page } } },
    {
      enabled: docId !== undefined,
      staleTime: Infinity,
      retry: false,
      placeholderData: keepPreviousData,
    },
  );
}

/**
 * The flow that sends questions and follows them. The writes go through mutations, so the
 * client sees every failure (a 401 sends the person to sign in); the lookup of an ask goes
 * through the query client for the same reason, and is never kept (it holds the answer).
 */
export function useAskFlow(eventSource?: EventSourceFactory): AskFlow {
  const queryClient = useQueryClient();
  const create = useMutation({
    mutationFn: async (body: AskRequest) => {
      const { data } = await fetchClient.POST(ASKS, { body });
      return data;
    },
  });
  const cancel = useMutation({
    mutationFn: async (askId: string) => {
      await fetchClient.DELETE(ASK, { params: { path: { ask_id: askId } } });
    },
  });
  const { mutateAsync: createAsk } = create;
  const { mutateAsync: cancelAsk } = cancel;
  return useMemo(
    () =>
      createAskFlow({
        create: async (body) => {
          const data = await createAsk(body);
          if (!data) throw new Error("The API answered without a body.");
          return data;
        },
        get: (askId) =>
          queryClient.fetchQuery({
            queryKey: ["asks", askId],
            gcTime: 0,
            staleTime: 0,
            queryFn: async () => {
              const { data } = await fetchClient.GET(ASK, { params: { path: { ask_id: askId } } });
              if (!data) throw new Error("The API answered without a body.");
              return data;
            },
          }),
        cancel: cancelAsk,
        // A question that works shows the engine is up: look at the status now, not at the next poll.
        answered: () =>
          void queryClient.invalidateQueries({
            queryKey: $api.queryOptions("get", STATUS, {}).queryKey,
          }),
        ...(eventSource ? { eventSource } : {}),
      }),
    [createAsk, cancelAsk, queryClient, eventSource],
  );
}

const TRANSCRIPTIONS = "/api/v1/speech/transcriptions";

/** Turns a recorded clip into text. The API stores neither the clip nor the text. */
export function useTranscribe() {
  return useMutation({
    // The clip and the text are not kept after the call.
    gcTime: 0,
    mutationFn: async ({ wav }: { wav: Uint8Array }) => {
      const { data } = await fetchClient.POST(TRANSCRIPTIONS, {
        // The contract types the body as a string; the bytes go out as they are.
        body: wav as unknown as string,
        bodySerializer: (body: unknown) => body,
        headers: { "Content-Type": "audio/wav" },
      });
      if (!data) throw new Error("The API answered without a body.");
      return data.text;
    },
  });
}
