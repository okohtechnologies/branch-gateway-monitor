import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import { LiveProvider } from "./live";
import { ToastProvider } from "./toast";
import { Layout } from "./components/Layout";
import { Login } from "./pages/Login";
import { Overview } from "./pages/Overview";
import { Branches } from "./pages/Branches";
import { Incidents } from "./pages/Incidents";

// Charts are the heaviest dependency: only load them when a branch page is opened.
const BranchDetail = lazy(() => import("./pages/BranchDetail").then((m) => ({ default: m.BranchDetail })));

function Gate() {
  const { user, loading } = useAuth();
  if (loading) return <div className="p-8 text-muted" role="status">Loading…</div>;
  if (!user) return <Login />;
  // The live feed only connects once signed in, and tears down on sign out.
  return (
    <LiveProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Overview />} />
          <Route path="branches" element={<Branches />} />
          <Route
            path="branches/:id"
            element={
              <Suspense fallback={<div className="skeleton h-64" aria-busy="true" />}>
                <BranchDetail />
              </Suspense>
            }
          />
          <Route path="incidents" element={<Incidents />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </LiveProvider>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          <Gate />
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
