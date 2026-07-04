import { ChatPanel } from "@/components/chat/ChatPanel";

export default function ChatPage() {
  return (
    <section
      aria-label="Chat"
      className="mx-auto flex h-[calc(100vh-57px)] w-full max-w-3xl flex-col px-4 py-4"
    >
      <ChatPanel />
    </section>
  );
}
