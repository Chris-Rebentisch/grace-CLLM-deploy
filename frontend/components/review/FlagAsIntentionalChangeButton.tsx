"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { ChangeDirectiveAuthoringForm } from "@/components/change-directives/ChangeDirectiveAuthoringForm";
import { COPY } from "@/lib/change-directives/copy";

export type FlagAsIntentionalChangeButtonProps = {
  sessionId: string;
  elementName: string;
};

export function FlagAsIntentionalChangeButton({
  sessionId,
  elementName,
}: FlagAsIntentionalChangeButtonProps) {
  const [open, setOpen] = React.useState(false);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="sm"
          data-testid="flag-intentional-change"
        >
          {COPY.flagButton.label}
        </Button>
      </SheetTrigger>
      <SheetContent side="right" data-testid="flag-drawer">
        <SheetHeader>
          <SheetTitle>{COPY.flagButton.title}</SheetTitle>
          <SheetDescription>{COPY.flagButton.description}</SheetDescription>
        </SheetHeader>
        <ChangeDirectiveAuthoringForm
          sessionId={sessionId}
          flaggedFromElementName={elementName}
          defaultAffectedSegments={[elementName]}
          onCreated={() => setOpen(false)}
          onCancel={() => setOpen(false)}
        />
      </SheetContent>
    </Sheet>
  );
}
