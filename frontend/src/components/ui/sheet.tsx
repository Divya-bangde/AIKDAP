import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import * as React from "react";

import { Dialog, DialogOpenContext, DialogOverlay } from "@/components/ui/dialog";
import { drawerVariants } from "@/lib/motion";
import { cn } from "@/lib/utils";

/** A right-side panel: the same Radix dialog as `Dialog` (focus trap,
 * Escape, scroll lock), with the drawer slide-in instead of the
 * centred scale. `Sheet` is `Dialog` itself, so open state is mirrored
 * the same way and the exit animation finishes before unmount. */
export const Sheet = Dialog;
export const SheetTitle = DialogPrimitive.Title;
export const SheetDescription = DialogPrimitive.Description;

export const SheetContent = React.forwardRef<
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
              variants={drawerVariants}
              initial="hidden"
              animate="visible"
              exit="exit"
              className={cn(
                "material-surface fixed inset-y-0 right-0 z-50 flex w-full flex-col shadow-float focus:outline-none",
                className,
              )}
            >
              {children}
              <DialogPrimitive.Close className="press absolute right-4 top-4 rounded-sm p-1 opacity-70 hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring">
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
SheetContent.displayName = "SheetContent";
