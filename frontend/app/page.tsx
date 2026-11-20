// The page shell is prerendered; the dashboard reads ?job=<id> in the browser, so it sits
// inside a Suspense boundary (required by Next.js for useSearchParams).
import { Suspense } from "react";

import Dashboard from "@/app/components/Dashboard";
import DashboardSkeleton from "@/app/components/DashboardSkeleton";

export default function Home() {
  return (
    <Suspense fallback={<DashboardSkeleton />}>
      <Dashboard />
    </Suspense>
  );
}
