type PageProps = {
  params: Promise<{ sessionId: string }>;
};

export default async function SessionResumePage({ params }: PageProps) {
  const { sessionId } = await params;
  return (
    <section
      aria-label="Session"
      className="mx-auto flex h-[calc(100vh-57px)] w-full max-w-3xl flex-col px-4 py-4"
    >
      <div className="text-sm text-muted-foreground">
        Session {sessionId.slice(0, 8)}… resume surface (populated in CP5/CP8).
      </div>
    </section>
  );
}
