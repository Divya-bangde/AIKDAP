import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import * as React from "react";

import { cn } from "@/lib/utils";
import { dialogVariants, overlayVariants } from "@/lib/motion";

/** Whether the dialog is currently open.
 *
 * Radix keeps its open state private, but `AnimatePresence` has to see
 * the surface leave in order to animate the exit — an unmount Radix
 * performs itself is invisible to it. So `Dialog` mirrors the state it
 * passes to Radix into this context, and `DialogContent` mounts with
 * `forceMount` and lets `AnimatePresence` own the removal instead.
 *
 * Mirroring rather than replacing: Radix still drives focus trapping,
 * scroll locking, `aria-*` wiring and Escape handling. The only thing
 * taken from it is *when the node disappears*. */
export const DialogOpenContext = React.createContext(false);

type DialogProps = React.ComponentPropsWithoutRef<typeof DialogPrimitive.Root>;

/** Supports both controlled (`open`) and uncontrolled (`defaultOpen`)
 * use, because both are in the codebase — `CreateProjectDialog` holds
 * its own state, `DocumentCard` does not. */
export function Dialog({
  open,
  defaultOpen,
  onOpenChange,
  children,
  ...props
}: DialogProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = React.useState(defaultOpen ?? false);
  const isControlled = open !== undefined;
  const isOpen = isControlled ? open : uncontrolledOpen;

  const handleOpenChange = React.useCallback(
    (next: boolean) => {
      if (!isControlled) setUncontrolledOpen(next);
      onOpenChange?.(next);
    },
    [isControlled, onOpenChange],
  );

  return (
    <DialogPrimitive.Root open={isOpen} onOpenChange={handleOpenChange} {...props}>
      <DialogOpenContext.Provider value={isOpen}>{children}</DialogOpenContext.Provider>
    </DialogPrimitive.Root>
  );
}

export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

export const DialogOverlay = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Overlay ref={ref} asChild forceMount {...props}>
    <motion.div
      variants={overlayVariants}
      initial="hidden"
      animate="visible"
      exit="exit"
      className={cn("fixed inset-0 z-50 bg-black/60", className)}
    />
  </DialogPrimitive.Overlay>
));
DialogOverlay.displayName = DialogPrimitive.Overlay.displayName;

export const DialogContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>
>(({ className, children, ...props }, ref) => {
  const isOpen = React.useContext(DialogOpenContext);

  return (
    <AnimatePresence>
      {isOpen ? (
        <DialogPrimitive.Portal forceMount>
          <DialogOverlay />
          <DialogPrimitive.Content ref={ref} asChild forceMount {...props}>
            <motion.div
              variants={dialogVariants}
              initial="hidden"
              animate="visible"
              exit="exit"
              /* Centred with `inset-0` and auto margins rather than
               * `left-1/2 top-1/2 -translate-1/2`. The translate
               * version owns the element's `transform`, which is the
               * property the scale animation needs — the two would
               * overwrite each other and the dialog would jump to the
               * top-left corner as it animated. Auto margins centre
               * without touching transform at all. */
              className={cn(
                "fixed inset-0 z-50 m-auto grid h-fit max-h-[85vh] w-[calc(100%-2rem)] max-w-lg gap-4 overflow-y-auto rounded-card bg-card p-7 shadow-float contrast-more:border contrast-more:border-border-strong",
                className,
              )}
            >
              {children}
              <DialogPrimitive.Close className="press absolute right-4 top-4 rounded-sm opacity-70 hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2">
                <X className="h-4 w-4" />
                <span className="sr-only">Close</span>
              </DialogPrimitive.Close>
            </motion.div>
          </DialogPrimitive.Content>
        </DialogPrimitive.Portal>
      ) : null}
    </AnimatePresence>
  );
});
DialogContent.displayName = DialogPrimitive.Content.displayName;

export function DialogHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex flex-col gap-1.5 text-left", className)} {...props} />;
}

export function DialogFooter({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("flex flex-col-reverse gap-2 sm:flex-row sm:justify-end", className)}
      {...props}
    />
  );
}

export const DialogTitle = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title
    ref={ref}
    className={cn("text-lg font-semibold leading-none tracking-tight", className)}
    {...props}
  />
));
DialogTitle.displayName = DialogPrimitive.Title.displayName;

export const DialogDescription = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Description
    ref={ref}
    className={cn("text-sm text-muted-foreground", className)}
    {...props}
  />
));
DialogDescription.displayName = DialogPrimitive.Description.displayName;
