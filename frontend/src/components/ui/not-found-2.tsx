import { CompassIcon, HomeIcon } from "lucide-react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";

export function NotFound() {
  return (
    <div className="relative flex min-h-screen w-full items-center justify-center overflow-hidden">
      <Empty>
        <EmptyHeader>
          {/* The upstream component fades the numeral with Tailwind v4
           * `mask-b-*` utilities, which do not exist in v3.4. A clipped
           * gradient gives the same falloff on the classes we have. */}
          <EmptyTitle className="bg-gradient-to-b from-foreground to-foreground/20 bg-clip-text text-9xl font-extrabold text-transparent">
            404
          </EmptyTitle>
          <EmptyDescription className="-mt-8 text-nowrap text-foreground/80">
            The page you&apos;re looking for might have been <br />
            moved or doesn&apos;t exist.
          </EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <div className="flex gap-2">
            {/* Router links, not `<a href>`: an in-app 404 should not
             * cost a full document reload to get back. */}
            <Button asChild>
              <Link to="/">
                <HomeIcon data-icon="inline-start" />
                Go Home
              </Link>
            </Button>

            <Button asChild variant="outline">
              <Link to="/projects">
                <CompassIcon data-icon="inline-start" />
                Explore
              </Link>
            </Button>
          </div>
        </EmptyContent>
      </Empty>
    </div>
  );
}
