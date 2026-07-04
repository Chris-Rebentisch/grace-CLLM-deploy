import { QueryClient } from "@tanstack/react-query";

// §3.3 EC-4 refetch guards. Belt-and-suspenders with the D196 Open-phase
// guard — TanStack Query itself is configured not to refetch anything in
// the background.
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
        refetchOnMount: false,
        staleTime: Number.POSITIVE_INFINITY,
        retry: 1,
      },
      mutations: {
        retry: 0,
      },
    },
  });
}
