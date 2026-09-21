import { PageTransition } from "@/components/motion/PageTransition";
import { DocumentsSection } from "@/features/assets/DocumentsSection";

/** Every document across the user's projects — where the Command
 * Center's "Documents" tile lands. */
export function Documents() {
  return (
    <PageTransition>
      <div className="flex flex-col gap-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Documents</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Every document uploaded across your projects.
          </p>
        </div>
        <DocumentsSection />
      </div>
    </PageTransition>
  );
}
